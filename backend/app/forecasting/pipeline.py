"""End-to-end forecast cycle:
   ERP panel -> segmentation -> backtest/champions -> calibrated baseline (units) -> CRM net uplift -> hybrid
   -> MinT (point + probabilistic) -> ASP -> revenue -> persistence (+ benchmark, registry, drift, risk)."""
from __future__ import annotations

import hashlib
import logging
import platform
import uuid
from collections.abc import Callable
from datetime import date

import lightgbm
import numpy as np
import pandas as pd
import statsforecast
from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.calendar import add_months
from app.core.config import get_settings
from app.core.workspace import workspace_kind
from app.core.settings_store import get_setting
from app.forecasting import data_access as da
from app.mapping.service import load_mapping_table
from app.ml import asp as asp_mod
from app.ml import backtest as bt_mod
from app.ml import commercial as com
from app.ml import reconcile as rec
from app.ml.base import qcol
from app.ml.models.registry import model_catalog
from app.ml.models.stats import AutoETSModel
from app.ml.segmentation import SEGMENT_PRIORS, classify
from app.models.forecast import ForecastPoint, ForecastRun, ModelBenchmark, ModelRegistryEntry, SeriesSegment, UpliftDetail
from app.models.reference import SystemSetting
from app.quality import checks, drift

log = logging.getLogger(__name__)
FAST_MODELS = ["Naive", "SeasonalNaive", "AutoETS", "CrostonSBA", "TSB", "LightGBM"]
Progress = Callable[[float, str], None]


class DataQualityBlocked(RuntimeError):
    pass


def _parse(uid: str) -> tuple[str, str, str]:
    o, r, p = uid.split("|")
    return o, r, p


def _final_baseline(long_df: pd.DataFrame, champs: dict, segs: dict[str, str], bt: bt_mod.BacktestResult, catalog: dict, horizon: int,
                    avail: dict) -> tuple[pd.DataFrame, dict[str, str]]:
    """Fit each needed champion on the full history and pick, per node & horizon, its segment-bucket champion."""
    needed = {v["model"] for v in champs.values()} | {"AutoETS"}
    if "Ensemble" in needed:
        needed |= set(bt.ensemble_members)
    frames, failed = {}, {}
    for name in sorted(needed - {"Ensemble"}):
        ok, why = avail.get(name, (True, ""))
        if not ok:
            failed[name] = why
            continue
        try:
            frames[name] = catalog[name][0]().fit(long_df, "y", "ds").predict(horizon).frame.set_index(["unique_id", "h"])
        except Exception as e:  # noqa: BLE001
            failed[name] = f"{type(e).__name__}: {e}"
            log.warning("final fit failed for %s: %s", name, e)
    if "Ensemble" in needed:
        mem = [frames[m] for m in bt.ensemble_members if m in frames]
        if mem:
            frames["Ensemble"] = sum(f[["ds", "mean", "q10", "q50", "q90"]].drop(columns="ds") for f in mem) / len(mem)
            frames["Ensemble"]["ds"] = mem[0]["ds"]
    base = frames["AutoETS"] if "AutoETS" in frames else next(iter(frames.values()))
    rows, used = [], {}
    for (uid, h), r0 in base.iterrows():
        m = bt_mod.champion_for(champs, segs[uid], int(h))
        src = frames.get(m)
        if src is None or (uid, h) not in src.index:
            m, src = "AutoETS", base
        r = src.loc[(uid, h)]
        rows.append((uid, r["ds"], int(h), r["mean"], r["q10"], r["q90"], m))
        used[uid] = m
    out = pd.DataFrame(rows, columns=["unique_id", "ds", "h", "mean", "q10", "q90", "model"])
    out["q10"] = np.minimum(out.q10, out["mean"])
    out["q90"] = np.maximum(out.q90, out["mean"])
    return out, failed


def run_forecast(session: Session, cycle_month: date, kind: str = "CURRENT", mode: str = "full", user: str = "system",
                 horizon: int | None = None, progress: Progress | None = None) -> str:
    cfg = get_settings()
    H = horizon or cfg.horizon
    pg = progress or (lambda f, m: None)
    run = ForecastRun(id=str(uuid.uuid4()), cycle_month=cycle_month, horizon=H, kind=kind, status="RUNNING", is_synthetic=workspace_kind(session) == "synthetic",
                      config={"mode": mode, "reconcile_method": cfg.reconcile_method, "mc_samples": cfg.mc_samples}, created_by=user)
    session.add(run)
    session.flush()
    try:
        _run(session, run, cycle_month, kind, mode, H, cfg, pg)
        run.status = "COMPLETED"
    except Exception as e:  # noqa: BLE001
        run.status, run.error = "FAILED", f"{type(e).__name__}: {e}"
        session.commit()
        raise
    audit(session, user, "FORECAST_RUN", "forecast_run", run.id, None, {"cycle": str(cycle_month), "kind": kind, "mode": mode, "summary": run.summary.get("headline")})
    session.commit()
    return run.id


def _run(session: Session, run: ForecastRun, cycle_month: date, kind: str, mode: str, H: int, cfg, pg: Progress) -> None:
    rng = np.random.default_rng(cfg.random_seed + cycle_month.year * 12 + cycle_month.month)
    # ------------------------------------------------------------------ 0. DQ gate
    if kind == "CURRENT":
        dq = checks.run_dq(session, cycle_month, H)
        blockers = checks.blocking_errors(session, dq)
        if blockers:
            raise DataQualityBlocked("; ".join(b["message"] for b in blockers))
    pg(0.03, "Loading panel")
    panel = da.load_panel(session, cycle_month)
    hier, S = panel.hier, panel.hier.S
    ids, T = hier.ids, len(panel.months)
    bottom_ids = ids[-hier.n_bottom:]
    long = panel.long("units")
    run.data_hash = hashlib.sha256(pd.util.hash_pandas_object(panel.units_bottom).to_numpy().tobytes()).hexdigest()
    # ------------------------------------------------------------------ 1. CRM + segmentation
    pg(0.06, "Segmenting series")
    crm = da.load_crm(session, cycle_month)
    drivers = da.pipeline_drivers(crm, panel.months, hier)
    cuts = dict(adi_cut=get_setting(session, "segment_adi_cutoff"), cv2_cut=get_setting(session, "segment_cv2_cutoff"),
                seas_min=get_setting(session, "segment_seasonality_min"), acf_min=get_setting(session, "segment_seasonal_acf_min"), exog_min=get_setting(session, "segment_exog_min"))
    stats = {u: classify(panel.units_nodes[u].to_numpy(), drivers[u], **cuts) for u in ids}
    segs = {u: s.segment for u, s in stats.items()}
    # ------------------------------------------------------------------ 2. backtest + champions
    pg(0.10, "Backtesting models")
    catalog = model_catalog(H)
    avail = {k: v[1].is_available() for k, v in catalog.items()}
    names = [n for n in catalog if mode == "full" or n in FAST_MODELS]
    bt = bt_mod.run_backtest(long, {n: catalog[n][0] for n in names}, horizon=H, availability=avail)
    bt = bt_mod.add_ensemble(bt)
    by_h, by_b = bt_mod.score_backtest(bt, segs)
    champs = bt_mod.select_champions(by_b, SEGMENT_PRIORS, float(get_setting(session, "champion_prior_tolerance")))
    champ_bt = bt_mod.champion_backtest_frame(bt, segs, champs)
    cal = bt_mod.fit_conformal(champ_bt)
    cal_champ = bt_mod.apply_conformal(champ_bt, segs, cal)
    bt.frame.to_parquet(cfg.artifact_dir / f"backtest_{run.id}.parquet")
    pg(0.45, "Fitting champion models")
    # ------------------------------------------------------------------ 3. calibrated baseline
    base, failed = _final_baseline(long, champs, segs, bt, catalog, H, avail)
    base = bt_mod.apply_conformal(base, segs, cal)
    fdates = pd.DatetimeIndex(sorted(base.ds.unique()))
    piv = lambda col: base.pivot(index="unique_id", columns="h", values=col).reindex(ids)  # noqa: E731
    b_mean, b_q10, b_q90 = (piv(c).to_numpy() for c in ("mean", "q10", "q90"))
    # ------------------------------------------------------------------ 4. ASP engine
    pg(0.60, "ASP engine")
    policy, base_month = get_setting(session, "fx_policy"), get_setting(session, "fx_base_month")
    asp_hist = asp_mod.asp_history(panel.units_bottom, panel.rev_bottom)
    mapping = load_mapping_table(session)
    acct = mapping[(mapping.valid_from <= pd.Timestamp(cycle_month)) & (mapping.valid_to.isna() | (mapping.valid_to > pd.Timestamp(cycle_month)))]
    from app.models.facts import Contract

    contracts = pd.DataFrame(session.execute(select(Contract.account_id, Contract.product_code, Contract.start_month, Contract.end_month,
                                                    Contract.committed_units_per_month, Contract.contract_price_local, Contract.currency,
                                                    Contract.annual_price_change_pct)).all(),
                             columns=["account_id", "product_code", "start_month", "end_month", "committed_units_per_month", "contract_price_local", "currency", "annual_price_change_pct"])
    fxw = da.fx_wide(session)
    cp, cu = (asp_mod.contract_price_panel(contracts, acct, fdates, fxw, policy, pd.Timestamp(base_month) if base_month else panel.months[-1])
              if len(contracts) else (pd.DataFrame(), pd.DataFrame()))
    base_units_bottom = pd.DataFrame(b_mean[-hier.n_bottom:].T, index=fdates, columns=bottom_ids)
    aspf = asp_mod.forecast_asp(asp_hist, H, cp, cu, base_units_bottom)
    asp_bt = asp_mod.backtest_asp(asp_hist, panel.units_bottom) if mode == "full" or T >= 24 else {"wmape": None}
    # ------------------------------------------------------------------ 5. commercial uplift (net of baseline)
    pg(0.68, "Commercial signal engine")
    champ_bottom = champ_bt[champ_bt.unique_id.isin(bottom_ids)]
    if crm.snaps.empty:
        net, cm, up = com.no_crm_result(bottom_ids, pd.Timestamp(cycle_month), H, cfg.mc_samples)
    else:
        net = com.estimate_net_factor(crm, bottom_ids, champ_bottom, H, seed=cfg.random_seed)
        cm = com.train_commercial_model(crm, pd.Timestamp(cycle_month), cfg.random_seed)
        up = com.compute_uplift(crm, pd.Timestamp(cycle_month), cm, bottom_ids, H, n_samples=cfg.mc_samples, seed=cfg.random_seed)
    beta_h = np.array([net["beta"].get(bt_mod.bucket_of(h), 0.0) for h in range(1, H + 1)])
    gross_b = up.expected.to_numpy().T  # (m,H)
    gross_n = S @ gross_b  # (n,H)
    # ------------------------------------------------------------------ 6. hybrid + MinT
    pg(0.78, "Reconciling hierarchy (MinT)")
    hyb_mean = b_mean + beta_h[None, :] * gross_n
    fit_resid = AutoETSModel().fit(long, "y", "ds").fitted_residuals()
    R = fit_resid.pivot(index="unique_id", columns="ds", values="resid").reindex(ids).fillna(0.0)
    Y_in = panel.units_nodes[ids].to_numpy().T
    resid = R.reindex(columns=panel.months).fillna(0.0).to_numpy()
    rr = rec.fit_reconciler(S, resid, cfg.reconcile_method, "hierarchicalforecast", y_insample=Y_in, y_hat_insample=Y_in - resid, tags=hier.tags)
    coh_mean = rec.reconcile_point(S, rr.P, hyb_mean)
    z = rec.gaussian_copula_z(resid, cfg.mc_samples, H, rng)
    base_samp = rec.two_piece_samples(b_mean, b_q10, b_q90, z)
    up_nodes = (S @ up.samples.reshape(hier.n_bottom, -1)).reshape(len(ids), H, -1)
    hyb_samp = base_samp + beta_h[None, :, None] * up_nodes
    coh_samp, bot_samp = rec.project_samples(S, rr.P, hyb_samp)
    u10, u90 = np.quantile(coh_samp, 0.1, axis=2), np.quantile(coh_samp, 0.9, axis=2)
    u10, u90 = np.minimum(u10, coh_mean), np.maximum(u90, coh_mean)
    coh_err = rec.coherence_error(S, coh_mean)
    # ------------------------------------------------------------------ 7. revenue = units x ASP
    asp_mean = aspf.mean.reindex(columns=bottom_ids).to_numpy().T  # (m,H)
    asp_sig = aspf.sigma_log.reindex(columns=bottom_ids).to_numpy().T
    asp_mean = np.where(np.isfinite(asp_mean), asp_mean, np.nanmean(asp_mean))
    eps = rng.standard_normal(bot_samp.shape)
    asp_samp = asp_mean[:, :, None] * np.exp(asp_sig[:, :, None] * eps - 0.5 * asp_sig[:, :, None] ** 2)
    rev_bot_samp = bot_samp * asp_samp
    rev_samp = (S @ rev_bot_samp.reshape(hier.n_bottom, -1)).reshape(len(ids), H, -1)
    bot_pt = coh_mean[-hier.n_bottom:]
    rev_pt_b = bot_pt * asp_mean
    rev_pt = S @ rev_pt_b
    r10, r90 = np.quantile(rev_samp, 0.1, axis=2), np.quantile(rev_samp, 0.9, axis=2)
    r10, r90 = np.minimum(r10, rev_pt), np.maximum(r90, rev_pt)
    asp_node = np.divide(rev_pt, coh_mean, out=np.zeros_like(rev_pt), where=coh_mean > 1e-9)
    # ------------------------------------------------------------------ 8. persist
    pg(0.88, "Persisting")
    levels = hier.levels()
    model_by = base.set_index(["unique_id", "h"])["model"]
    pts = []
    for i, uid in enumerate(ids):
        o, r, p = _parse(uid)
        for h in range(1, H + 1):
            pts.append(dict(run_id=run.id, level=levels[i], oem_code=o, region_code=r, product_code=p, month=fdates[h - 1].date(), horizon=h,
                            units_p10=float(u10[i, h - 1]), units_p50=float(coh_mean[i, h - 1]), units_p90=float(u90[i, h - 1]),
                            baseline_units_p50=float(b_mean[i, h - 1]), uplift_units=float(beta_h[h - 1] * gross_n[i, h - 1]),
                            gross_uplift_units=float(gross_n[i, h - 1]), asp_usd=float(asp_node[i, h - 1]),
                            revenue_p10=float(r10[i, h - 1]), revenue_p50=float(rev_pt[i, h - 1]), revenue_p90=float(r90[i, h - 1]),
                            model_name=model_by.get((uid, h)), segment=segs[uid]))
    session.execute(insert(ForecastPoint), pts)
    scen = session.get(SystemSetting, "synthetic_scenarios")
    champ_final = {u: model_by.get((u, 1)) for u in ids}
    session.execute(insert(SeriesSegment), [dict(run_id=run.id, level=levels[i], oem_code=_parse(u)[0], region_code=_parse(u)[1], product_code=_parse(u)[2],
                                                 n_obs=stats[u].n_obs, adi=_f(stats[u].adi), cv2=_f(stats[u].cv2), seasonality_strength=stats[u].seasonality_strength,
                                                 acf12=stats[u].acf12, exog_strength=stats[u].exog_strength, segment=stats[u].segment,
                                                 champion_model=champ_final[u], scenario_tag=(scen.value.get(u) if scen and levels[i] == "BOTTOM" else None))
                                            for i, u in enumerate(ids)])
    if len(up.detail):
        d = up.detail.copy()
        d["month"] = pd.to_datetime(d["month"]).dt.date
        sf = crm.opps.set_index("id")
        recs = []
        for r in d.itertuples(index=False):
            o, rg, p = _parse(r.bottom_id)
            recs.append(dict(run_id=run.id, opportunity_id=r.opportunity_id, sfdc_id=f"006{r.opportunity_id:08d}", oem_code=o, region_code=rg, product_code=p,
                             month=r.month, stage=r.stage, win_prob=r.win_prob, rep_probability=r.rep_probability, expected_units=r.expected_units,
                             unweighted_units=r.unweighted_units))
        session.execute(insert(UpliftDetail), recs)
    _persist_benchmark(session, run.id, by_h, bt, champs, avail, segs)
    ver = {"lightgbm": lightgbm.__version__, "statsforecast": statsforecast.__version__, "python": platform.python_version()}
    for m in sorted({v["model"] for v in champs.values()}):
        session.add(ModelRegistryEntry(run_id=run.id, model_name=m, version=run.id[:8], params={"horizon": H}, library_versions=ver,
                                       metrics={f"{k[0]}|b{k[1]}": float(v["wmape"]) for k, v in champs.items() if v["model"] == m}))
    live = None
    drift.feature_drift(session, run.id, panel.units_bottom)
    allc = by_b[(by_b.segment == "ALL")]
    champ_overall = float(np.nanmean([c["wmape"] for k, c in champs.items() if k[0] == "ALL"])) if champs else None
    drift.error_drift(session, run.id, live, champ_overall)
    cov = float(((cal_champ.y >= cal_champ.q10) & (cal_champ.y <= cal_champ.q90)).mean())
    seg_counts = pd.Series(segs).value_counts().to_dict()
    run.summary = {
        "headline": {"champion_backtest_wmape": champ_overall, "calibrated_p10_p90_coverage": cov, "asp_wmape": asp_bt.get("wmape"),
                     "net_uplift_beta": net["beta"], "uplift_improvement": net.get("improvement")},
        "segments": seg_counts, "champions": {f"{k[0]}|b{k[1]}": v for k, v in champs.items()},
        "model_status": {k: list(v) for k, v in bt.status.items()}, "final_fit_failures": failed,
        "reconciliation": {"method_requested": cfg.reconcile_method, "method_used": rr.method_used, "attempts": rr.attempts, "lambda": rr.shrinkage_lambda,
                           "max_coherence_error": coh_err},
        "net_uplift": net, "commercial_model": cm.diagnostics, "uplift": up.diagnostics, "asp_backtest": asp_bt, "aspf": aspf.diagnostics,
        "conformal": {f"{k[0]}|b{k[1]}": v for k, v in cal.items()}, "backtest_origins": [str(o.date()) for o in bt.origins], "n_nodes": len(ids), "timings": bt.timings,
    }
    session.flush()
    pg(0.95, "Risk engine")
    from app.risk.engine import refresh_alerts

    refresh_alerts(session, run.id)
    pg(1.0, "Done")


def _f(x: float) -> float:
    return float(x) if np.isfinite(x) else 9999.0


def _persist_benchmark(session: Session, run_id: str, by_h: pd.DataFrame, bt: bt_mod.BacktestResult, champs: dict, avail: dict, segs: dict) -> None:
    fam = {n: v[1].family for n, v in model_catalog().items()}
    fam["Ensemble"] = "Ensemble"
    prior_txt = {s: ", ".join(p) for s, p in SEGMENT_PRIORS.items()}
    rows = []
    for r in by_h.itertuples(index=False):
        ch = champs.get((r.segment, bt_mod.bucket_of(r.horizon)))
        rows.append(dict(run_id=run_id, model_name=r.model, model_family=fam.get(r.model, "Other"), segment=r.segment, horizon=int(r.horizon),
                         wmape=_n(r.wmape), mae=_n(r.mae), mase=_n(r.mase), bias=_n(r.bias), pinball=_n(r.pinball), coverage80=_n(r.coverage80), n_obs=int(r.n),
                         is_champion=bool(ch and ch["model"] == r.model), prior_models=prior_txt.get(r.segment), status="OK", note=None))
    for name, (st, note) in bt.status.items():
        if st != "OK":
            for h in bt_mod.BENCH_HORIZONS:
                rows.append(dict(run_id=run_id, model_name=name, model_family=fam.get(name, "Other"), segment="ALL", horizon=h, wmape=None, mae=None, mase=None,
                                 bias=None, pinball=None, coverage80=None, n_obs=0, is_champion=False, prior_models=None, status="UNAVAILABLE" if st == "UNAVAILABLE" else "FAILED", note=note))
    for name in set(avail) - set(bt.status):
        for h in bt_mod.BENCH_HORIZONS:
            rows.append(dict(run_id=run_id, model_name=name, model_family=fam.get(name, "Other"), segment="ALL", horizon=h, wmape=None, mae=None, mase=None, bias=None,
                             pinball=None, coverage80=None, n_obs=0, is_champion=False, prior_models=None, status="UNAVAILABLE",
                             note=avail[name][1] or "skipped in fast mode"))
    session.execute(insert(ModelBenchmark), rows)


def _n(x):
    return None if x is None or not np.isfinite(x) else float(x)
