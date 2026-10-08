"""Tier 2 - Probabilistic commercial signal engine (CRM -> expected incremental units).

 E[uplift units, month m] = sum_i  Qty_i * f_rep * P(win_i | stage, age, push-count, rep accuracy ...)  * P(deliver in m | slip(stage), delay(product), ramp)

 * point-in-time: every feature / distribution is built only from snapshots with snapshot_month <= as-of month
 * net-of-baseline: gross expected units are scaled by beta in [0,1], estimated on backtest residuals
   (history already contains the average effect of past pipeline -> adding gross uplift would double count)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.model_selection import GroupKFold

log = logging.getLogger(__name__)
SLIP_SUPPORT = np.arange(-3, 9)  # months between actual and (rep-)expected close
DELAY_SUPPORT = np.arange(0, 7)  # months between close and first delivery
FEATURES = ["stage", "months_in_stage", "age", "push_count", "rep_probability", "log_qty", "months_to_close", "quote_issued", "product_idx",
            "region_idx", "rep_win_rate", "rep_gap", "rep_n_closed", "qty_vs_first"]
STAGE_PRIOR = {1: 0.10, 2: 0.18, 3: 0.30, 4: 0.45, 5: 0.65}


def months_diff(a: pd.Series, b: pd.Series) -> np.ndarray:
    a, b = pd.to_datetime(a), pd.to_datetime(b)
    return ((a.dt.year - b.dt.year) * 12 + (a.dt.month - b.dt.month)).to_numpy()


@dataclass
class CrmData:
    opps: pd.DataFrame  # id, product_code, rep_id, account_id, end_account_id, created_month, ramp_months, quantity_units(true, only used post-delivery), first_delivery_month, region_code
    snaps: pd.DataFrame  # opportunity_id, snapshot_month, stage, quantity_units, expected_close_month, months_in_stage, push_count, rep_probability, quote_issued
    node_map: pd.DataFrame  # opportunity_id, oem_code, region_code, pct  (as-of mapping)
    products: list[str]
    regions: list[str]

    def asof(self, asof: pd.Timestamp) -> CrmData:
        return CrmData(self.opps, self.snaps[self.snaps.snapshot_month <= asof], self.node_map, self.products, self.regions)


# ------------------------------------------------------------------------------ point-in-time features
def closure_table(snaps: pd.DataFrame) -> pd.DataFrame:
    c = snaps[snaps.stage >= 6][["opportunity_id", "snapshot_month", "stage"]].rename(columns={"snapshot_month": "closed_month"})
    c["won"] = (c.stage == 6).astype(int)
    return c.drop(columns="stage")


def rep_stats_by_month(crm: CrmData) -> pd.DataFrame:
    """Per (rep, month): outcomes of that rep's opps closed STRICTLY BEFORE the month. Shrunk toward priors."""
    cl = closure_table(crm.snaps).merge(crm.opps[["id", "rep_id"]], left_on="opportunity_id", right_on="id")
    last_open = (crm.snaps[crm.snaps.stage <= 5].sort_values("snapshot_month").groupby("opportunity_id").tail(1)[["opportunity_id", "rep_probability"]]
                 .rename(columns={"rep_probability": "last_prob"}))
    cl = cl.merge(last_open, on="opportunity_id", how="left")
    months = pd.DatetimeIndex(sorted(crm.snaps.snapshot_month.unique()))
    out = []
    for rep, g in cl.groupby("rep_id"):
        agg = g.groupby("closed_month").agg(n=("won", "size"), w=("won", "sum"), p=("last_prob", "sum")).reindex(months, fill_value=0).cumsum().shift(1, fill_value=0)
        agg["rep_id"] = rep
        out.append(agg.reset_index().rename(columns={"index": "month"}))
    if not out:
        return pd.DataFrame(columns=["month", "rep_id", "rep_win_rate", "rep_gap", "rep_n_closed"])
    s = pd.concat(out)
    k, prior = 6.0, 0.30
    s["rep_win_rate"] = (s.w + prior * k) / (s.n + k)
    s["rep_gap"] = (s.p + prior * k) / (s.n + k) - s["rep_win_rate"]  # optimism: stated probability - realised win rate (shrunk)
    s["rep_n_closed"] = s.n
    return s[["month", "rep_id", "rep_win_rate", "rep_gap", "rep_n_closed"]]


def build_features(crm: CrmData, rows: pd.DataFrame | None = None) -> pd.DataFrame:
    sn = (crm.snaps if rows is None else rows).copy()
    o = crm.opps.set_index("id")
    sn = sn.join(o[["product_code", "rep_id", "created_month"]], on="opportunity_id")
    sn["age"] = months_diff(sn.snapshot_month, sn.created_month)
    sn["months_to_close"] = months_diff(sn.expected_close_month, sn.snapshot_month)
    sn["log_qty"] = np.log1p(sn.quantity_units)
    first_q = crm.snaps.sort_values("snapshot_month").groupby("opportunity_id").quantity_units.first()
    sn["qty_vs_first"] = sn.quantity_units / sn.opportunity_id.map(first_q)
    sn["product_idx"] = sn.product_code.map({p: i for i, p in enumerate(crm.products)})
    region = crm.node_map.sort_values("pct", ascending=False).drop_duplicates("opportunity_id").set_index("opportunity_id")["region_code"]
    sn["region_idx"] = sn.opportunity_id.map(region).map({r: i for i, r in enumerate(crm.regions)})
    rs = rep_stats_by_month(crm)
    sn = sn.merge(rs, left_on=["rep_id", "snapshot_month"], right_on=["rep_id", "month"], how="left").drop(columns="month", errors="ignore")
    sn["rep_win_rate"] = sn.rep_win_rate.fillna(0.30)
    sn["rep_gap"] = sn.rep_gap.fillna(0.0)
    sn["rep_n_closed"] = sn.rep_n_closed.fillna(0)
    sn["quote_issued"] = sn.quote_issued.astype(int)
    return sn


# ------------------------------------------------------------------------------ models
@dataclass
class CommercialModel:
    win_model: lgb.LGBMClassifier | None
    iso: IsotonicRegression | None
    slip: dict[int, np.ndarray]  # stage -> probs over SLIP_SUPPORT
    delay: dict[str, np.ndarray]  # product -> probs over DELAY_SUPPORT
    delay_pool: np.ndarray
    rep_qty_factor: dict[int, float]
    diagnostics: dict = field(default_factory=dict)

    def p_win(self, feats: pd.DataFrame) -> np.ndarray:
        if self.win_model is None:
            return feats["stage"].map(STAGE_PRIOR).to_numpy(dtype=float)
        p = self.win_model.predict_proba(feats[FEATURES])[:, 1]
        return np.clip(self.iso.predict(p), 0.01, 0.97) if self.iso is not None else p


def _smooth_counts(vals: np.ndarray, support: np.ndarray, alpha: float = 0.5) -> np.ndarray:
    v = np.clip(vals, support.min(), support.max())
    c = np.array([(v == s).sum() for s in support], dtype=float) + alpha
    return c / c.sum()


def train_commercial_model(crm: CrmData, asof: pd.Timestamp, seed: int = 7) -> CommercialModel:
    """Fit using only information available at `asof`."""
    crm = crm.asof(asof)
    cl = closure_table(crm.snaps)
    cl = cl[cl.closed_month <= asof]
    feats = build_features(crm)
    tr = feats[(feats.stage <= 5)].merge(cl, on="opportunity_id", how="inner")
    diag = {"train_rows": int(len(tr)), "closed_opps": int(len(cl)), "win_rate": float(cl.won.mean()) if len(cl) else None}
    win, iso = None, None
    if len(tr) >= 400 and tr.won.nunique() == 2:
        win = lgb.LGBMClassifier(n_estimators=140, learning_rate=0.05, num_leaves=8, min_child_samples=40, subsample=0.8, subsample_freq=1,
                                 colsample_bytree=0.8, reg_lambda=2.0, random_state=seed, verbose=-1, n_jobs=2)
        X, y, grp = tr[FEATURES], tr.won, tr.opportunity_id
        oof = np.zeros(len(tr))
        for a, b in GroupKFold(n_splits=3).split(X, y, grp):
            m = lgb.LGBMClassifier(**win.get_params()).fit(X.iloc[a], y.iloc[a])
            oof[b] = m.predict_proba(X.iloc[b])[:, 1]
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.01, y_max=0.97).fit(oof, y)
        win.fit(X, y)
        from sklearn.metrics import roc_auc_score

        diag["oof_auc"] = float(roc_auc_score(y, oof))
        diag["importance"] = dict(sorted(zip(FEATURES, map(int, win.booster_.feature_importance("gain"))), key=lambda kv: -kv[1])[:6])
    # slip(stage): actual close - expected close (as observed at each historical snapshot)
    joined = crm.snaps[crm.snaps.stage <= 5].merge(cl[cl.won == 1], on="opportunity_id")
    slip_all = months_diff(joined.closed_month, joined.expected_close_month)
    slip = {s: _smooth_counts(slip_all[joined.stage.to_numpy() == s], SLIP_SUPPORT) for s in range(1, 6)}
    # delay(product): first delivery - close (use opps closed long enough ago to have started delivering)
    o = crm.opps.merge(cl[cl.won == 1], left_on="id", right_on="opportunity_id")
    o = o[(o.closed_month <= asof - pd.DateOffset(months=6)) & o.first_delivery_month.notna() & (o.first_delivery_month <= asof)]
    d_all = months_diff(o.first_delivery_month, o.closed_month)
    pool = _smooth_counts(d_all, DELAY_SUPPORT)
    delay = {p: (_smooth_counts(d_all[(o.product_code == p).to_numpy()], DELAY_SUPPORT) if (o.product_code == p).sum() >= 10 else pool) for p in crm.products}
    # realised/reported quantity per rep (needs delivered deals) -> shrunk multiplicative factor
    last_open_q = crm.snaps[crm.snaps.stage <= 5].sort_values("snapshot_month").groupby("opportunity_id").quantity_units.last()
    done = o[(o.first_delivery_month + pd.to_timedelta(o.ramp_months * 30, unit="D")) <= asof].copy()
    done["ratio"] = done.quantity_units / done.id.map(last_open_q)
    f = {}
    for rep, g in done.groupby("rep_id"):
        n = len(g)
        f[int(rep)] = float((g.ratio.sum() + 3.0) / (n + 3.0))
    diag.update(slip_n=int(len(joined)), delay_n=int(len(o)), qty_factor_reps=len(f))
    return CommercialModel(win, iso, slip, delay, pool, f, diag)


# ------------------------------------------------------------------------------ uplift computation
@dataclass
class UpliftResult:
    detail: pd.DataFrame  # opportunity_id, bottom_id, month, stage, win_prob, rep_probability, expected_units, unweighted_units
    expected: pd.DataFrame  # months x bottom_ids  (gross expected units)
    samples: np.ndarray | None  # (n_bottom, H, N) gross uplift sample paths
    diagnostics: dict


def open_pipeline(crm: CrmData, asof: pd.Timestamp, model: CommercialModel) -> pd.DataFrame:
    """Opportunities open at `asof` (latest snapshot is at asof and stage <= 5) with calibrated win prob & expected quantity."""
    latest = crm.snaps[(crm.snaps.snapshot_month == asof) & (crm.snaps.stage <= 5)]
    f = build_features(crm.asof(asof), latest)
    f["win_prob"] = model.p_win(f)
    f["qty_adj"] = f.quantity_units * f.rep_id.map(model.rep_qty_factor).fillna(1.0)
    f = f.merge(crm.opps[["id", "ramp_months"]], left_on="opportunity_id", right_on="id")
    return f


def compute_uplift(crm: CrmData, asof: pd.Timestamp, model: CommercialModel, bottoms: list[str], horizon: int,
                   n_samples: int = 0, seed: int = 7) -> UpliftResult:
    pipe = open_pipeline(crm, asof, model)
    pipe = pipe.merge(crm.node_map, on="opportunity_id")  # expand distributor allocations
    bidx = {b: i for i, b in enumerate(bottoms)}
    months = pd.date_range(asof + pd.offsets.MonthBegin(1), periods=horizon, freq="MS")
    exp = np.zeros((len(bottoms), horizon))
    rng = np.random.default_rng(seed)
    samp = np.zeros((len(bottoms), horizon, n_samples)) if n_samples else None
    rows = []
    for r in pipe.itertuples(index=False):
        bid = f"{r.oem_code}|{r.region_code}|{r.product_code}"
        if bid not in bidx:
            continue
        b, R = bidx[bid], max(int(r.ramp_months), 1)
        qty = float(r.qty_adj) * float(r.pct)
        e_off = int(months_diff(pd.Series([r.expected_close_month]), pd.Series([asof]))[0])
        ps = model.slip[int(r.stage)]
        pd_ = model.delay.get(r.product_code, model.delay_pool)
        # distribution of first-delivery offset k (months after asof): close = max(e+slip, 1); f = close + delay
        pf = np.zeros(horizon + 30)
        for si, s in enumerate(SLIP_SUPPORT):
            c = max(e_off + int(s), 1)
            for di, d in enumerate(DELAY_SUPPORT):
                pf[min(c + int(d), len(pf) - 1)] += ps[si] * pd_[di]
        deliv = np.zeros(horizon)
        for k in range(1, len(pf)):
            if pf[k] == 0:
                continue
            for j in range(k, min(k + R, horizon + 1)):
                deliv[j - 1] += pf[k] / R
        g = qty * deliv  # units if won, per month
        exp[b] += float(r.win_prob) * g
        for j, m in enumerate(months):
            if g[j] > 1e-9:
                rows.append((int(r.opportunity_id), bid, m, int(r.stage), float(r.win_prob), float(r.rep_probability), float(r.win_prob * g[j]), float(g[j])))
        if samp is not None:
            win = rng.random(n_samples) < r.win_prob
            slip = rng.choice(SLIP_SUPPORT, size=n_samples, p=ps)
            dly = rng.choice(DELAY_SUPPORT, size=n_samples, p=pd_)
            f0 = np.maximum(e_off + slip, 1) + dly
            for k in range(R):
                j = f0 + k - 1
                ok = win & (j < horizon)
                if ok.any():
                    samp[b, j[ok], np.nonzero(ok)[0]] += qty / R
    detail = pd.DataFrame(rows, columns=["opportunity_id", "bottom_id", "month", "stage", "win_prob", "rep_probability", "expected_units", "unweighted_units"])
    diag = {"open_opps": int(pipe.opportunity_id.nunique()), "mean_win_prob": float(pipe.win_prob.mean()) if len(pipe) else None,
            "mean_rep_probability": float(pipe.rep_probability.mean()) if len(pipe) else None, "gross_expected_units": float(exp.sum())}
    return UpliftResult(detail, pd.DataFrame(exp.T, index=months, columns=bottoms), samp, diag)


# ------------------------------------------------------------------------------ net-of-baseline calibration
def estimate_net_factor(crm: CrmData, bottoms: list[str], champ_bt: pd.DataFrame, horizon: int, seed: int = 7, min_rows: int = 80) -> dict:
    """beta_b per horizon bucket via least squares on backtest residuals (actual - baseline) vs gross expected uplift
    computed point-in-time at every backtest origin. beta=0 if the hybrid does not beat the baseline in-sample."""
    bt = champ_bt[champ_bt.unique_id.isin(bottoms)].copy()
    recs = []
    for origin, g in bt.groupby("origin"):
        try:
            mdl = train_commercial_model(crm, origin, seed)
            up = compute_uplift(crm, origin, mdl, bottoms, horizon)
        except Exception as e:  # noqa: BLE001
            log.warning("uplift calibration skipped origin %s: %s", origin, e)
            continue
        e = up.expected.stack().rename("gross").reset_index()
        e.columns = ["ds", "unique_id", "gross"]
        recs.append(g.merge(e, on=["unique_id", "ds"], how="left").fillna({"gross": 0.0}))
    if not recs:
        return {"beta": {1: 0.0, 2: 0.0, 3: 0.0}, "n": 0, "improvement": 0.0, "note": "no calibration data"}
    d = pd.concat(recs, ignore_index=True)
    d["resid"] = d.y - d["mean"]
    d["bucket"] = np.where(d.h <= 3, 1, np.where(d.h <= 6, 2, 3))
    beta, out = {}, {}
    pooled_b = np.clip((d.gross * d.resid).sum() / max((d.gross ** 2).sum(), 1e-9), 0, 1) if (d.gross > 0).sum() >= min_rows else 0.0
    for b in (1, 2, 3):
        g = d[d.bucket == b]
        if (g.gross > 0).sum() >= min_rows:
            bb = float(np.clip((g.gross * g.resid).sum() / max((g.gross ** 2).sum(), 1e-9), 0, 1))
            n = int((g.gross > 0).sum())
            beta[b] = bb * n / (n + 40) + pooled_b * 40 / (n + 40)  # shrink toward pooled
        else:
            beta[b] = float(pooled_b)
    d["hyb"] = d["mean"] + d.bucket.map(beta) * d.gross
    base_err, hyb_err = float((d.y - d["mean"]).abs().sum()), float((d.y - d.hyb).abs().sum())
    improvement = 1 - hyb_err / base_err if base_err > 0 else 0.0
    if improvement <= 0:
        beta = {1: 0.0, 2: 0.0, 3: 0.0}
    return {"beta": {int(k): float(v) for k, v in beta.items()}, "n": int(len(d)), "improvement": float(improvement),
            "wmape_baseline": base_err / max(float(d.y.abs().sum()), 1e-9), "wmape_hybrid": hyb_err / max(float(d.y.abs().sum()), 1e-9),
            "note": "ok" if improvement > 0 else "uplift did not improve backtest accuracy -> net factor set to 0"}
