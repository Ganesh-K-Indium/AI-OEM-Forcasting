"""Read-side assembly for the UI: explorer series, detail drill-down, dashboard KPIs."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.governance.consensus import build_consensus
from app.ml.hierarchy import ALL, level_of
from app.models.facts import BacklogSnapshot, CapacityAllocation, MappedSeries, Opportunity
from app.models.forecast import ForecastPoint, ForecastRun, ModelBenchmark, SeriesSegment, UpliftDetail
from app.models.governance import AuditLog, ConsensusPoint, FvaResult, Override, PlanningCycle, RiskAlert
from app.risk.engine import coverage_table, has_rows


class NotFound(LookupError):
    pass


def latest_run(session: Session, kind: str = "CURRENT") -> ForecastRun | None:
    return session.execute(select(ForecastRun).where(ForecastRun.status == "COMPLETED", ForecastRun.kind == kind)
                           .order_by(ForecastRun.cycle_month.desc(), ForecastRun.created_at.desc())).scalars().first()


def resolve_run(session: Session, run_id: str | None) -> ForecastRun:
    run = session.get(ForecastRun, run_id) if run_id else latest_run(session)
    if run is None:
        raise NotFound("no completed forecast run - seed the demo or run a forecast first")
    return run


def _mask(df: pd.DataFrame, oem: str, region: str, product: str) -> pd.Series:
    m = pd.Series(True, index=df.index)
    if oem != ALL:
        m &= df.oem == oem
    if region != ALL:
        m &= df.region == region
    if product != ALL:
        m &= df["product"] == product
    return m


def explorer(session: Session, run: ForecastRun, oem: str = ALL, region: str = ALL, product: str = ALL, cons=None) -> dict:
    lvl = level_of(oem, region, product)
    if lvl == "INVALID":
        raise ValueError("unsupported node: choose OEM, Region, Product, OEM x Region, a single series or Total")
    fps = list(session.execute(select(ForecastPoint).where(ForecastPoint.run_id == run.id, ForecastPoint.oem_code == oem, ForecastPoint.region_code == region,
                                                          ForecastPoint.product_code == product).order_by(ForecastPoint.month)).scalars())
    if not fps:
        raise NotFound("node not found in this run")
    hist_rows = session.execute(select(MappedSeries.month, MappedSeries.oem_code, MappedSeries.region_code, MappedSeries.product_code, MappedSeries.units,
                                       MappedSeries.revenue_usd).where(MappedSeries.month <= run.cycle_month, MappedSeries.oem_code != "UNMAPPED")).all()
    h = pd.DataFrame(hist_rows, columns=["month", "oem", "region", "product", "units", "rev"])
    h = h[_mask(h, oem, region, product)].groupby("month", as_index=False)[["units", "rev"]].sum().sort_values("month")
    cres = cons or build_consensus(session, run.id)
    cb = cres.bottom
    cb = cb[_mask(cb, oem, region, product)].groupby("month", as_index=False)[["consensus_units", "consensus_revenue"]].sum().set_index("month")
    ov = {o.month: o for o in session.execute(select(Override).where(Override.run_id == run.id, Override.oem_code == oem, Override.region_code == region,
                                                                    Override.product_code == product, Override.status == "ACTIVE")).scalars()}
    ct = coverage_table(session, run, cres.bottom, with_thresholds=False)
    ct = ct[_mask(ct, oem, region, product)].groupby("month", as_index=False)[["bl_units", "bl_value", "consensus_units"]].sum().set_index("month")
    cap = pd.DataFrame(session.execute(select(CapacityAllocation.month, CapacityAllocation.region_code, CapacityAllocation.product_code, CapacityAllocation.capacity_units)).all(),
                       columns=["month", "region", "product", "cap"])
    if region != ALL:
        cap = cap[cap.region == region]
    if product != ALL:
        cap = cap[cap["product"] == product]
    cap = cap.groupby("month").cap.sum() if (oem == ALL and len(cap)) else pd.Series(dtype=float)
    seg = session.execute(select(SeriesSegment).where(SeriesSegment.run_id == run.id, SeriesSegment.oem_code == oem, SeriesSegment.region_code == region,
                                                      SeriesSegment.product_code == product)).scalar_one_or_none()
    fc = []
    for f in fps:
        c = cb.loc[f.month] if f.month in cb.index else None
        cov = ct.loc[f.month] if f.month in ct.index else None
        o = ov.get(f.month)
        fc.append(dict(month=f.month, horizon=f.horizon, units_p10=f.units_p10, units_p50=f.units_p50, units_p90=f.units_p90, revenue_p10=f.revenue_p10,
                       revenue_p50=f.revenue_p50, revenue_p90=f.revenue_p90, baseline_units=f.baseline_units_p50, uplift_units=f.uplift_units,
                       gross_uplift_units=f.gross_uplift_units, uplift_revenue=f.uplift_units * f.asp_usd, asp_usd=f.asp_usd,
                       override_revenue=o.override_revenue if o else None, override_units=o.override_units if o else None, override_reason=o.reason_code if o else None,
                       consensus_units=float(c.consensus_units) if c is not None else f.units_p50, consensus_revenue=float(c.consensus_revenue) if c is not None else f.revenue_p50,
                       backlog_value=float(cov.bl_value) if cov is not None else None,
                       coverage=float(cov.bl_units / cov.consensus_units) if cov is not None and cov.consensus_units > 1e-9 else None,
                       capacity_units=float(cap[f.month]) if f.month in cap.index else None, model_name=f.model_name))
    tot = dict(ai_revenue=sum(x["revenue_p50"] for x in fc), consensus_revenue=sum(x["consensus_revenue"] for x in fc), uplift_revenue=sum(x["uplift_revenue"] for x in fc),
               ai_units=sum(x["units_p50"] for x in fc), consensus_units=sum(x["consensus_units"] for x in fc))
    return dict(run_id=run.id, level=lvl, oem=oem, region=region, product=product, segment=seg.segment if seg else None, champion_model=seg.champion_model if seg else None,
                scenario_tag=seg.scenario_tag if seg else None, cycle_month=run.cycle_month, synthetic=run.is_synthetic,
                history=[dict(month=r.month, units=float(r.units), revenue=float(r.rev), asp=float(r.rev / r.units) if r.units > 0 else None) for r in h.itertuples()],
                forecast=fc, totals=tot)


def overlaps(o: Override, oem: str, region: str, product: str) -> bool:
    return all(a == b or a == ALL or b == ALL for a, b in ((o.oem_code, oem), (o.region_code, region), (o.product_code, product)))


def detail(session: Session, run: ForecastRun, oem: str, region: str, product: str) -> dict:
    cres = build_consensus(session, run.id)
    ex = explorer(session, run, oem, region, product, cres)
    ud = pd.DataFrame(session.execute(select(UpliftDetail.opportunity_id, UpliftDetail.sfdc_id, UpliftDetail.oem_code, UpliftDetail.region_code, UpliftDetail.product_code,
                                             UpliftDetail.stage, UpliftDetail.win_prob, UpliftDetail.rep_probability, UpliftDetail.expected_units, UpliftDetail.unweighted_units)
                                      .where(UpliftDetail.run_id == run.id)).all(),
                      columns=["opportunity_id", "sfdc_id", "oem", "region", "product", "stage", "win_prob", "rep_probability", "expected_units", "unweighted_units"])
    opps = []
    if len(ud):
        ud = ud[_mask(ud, oem, region, product)]
        g = ud.groupby(["opportunity_id", "sfdc_id", "oem", "region", "product", "stage"], as_index=False).agg(
            win_prob=("win_prob", "first"), rep_probability=("rep_probability", "first"), expected_units=("expected_units", "sum"), unweighted_units=("unweighted_units", "sum"))
        tot = g.expected_units.sum() or 1.0
        g["share_of_uplift"] = g.expected_units / tot
        opps = g.sort_values("expected_units", ascending=False).head(40).to_dict("records")
    ovs = [o for o in session.execute(select(Override).order_by(Override.timestamp.desc()).limit(500)).scalars() if overlaps(o, oem, region, product)][:50]
    scopes = {"ALL"} | ({f"oem:{oem}"} if oem != ALL else set()) | ({f"region:{region}"} if region != ALL else set()) | ({f"product:{product}"} if product != ALL else set())
    fva = list(session.execute(select(FvaResult).where(FvaResult.scope.in_(scopes), FvaResult.run_id == "ALL")).scalars())
    ids = [str(o.id) for o in ovs]
    audit = list(session.execute(select(AuditLog).where(AuditLog.entity_type == "override", AuditLog.entity_id.in_(ids)).order_by(AuditLog.id.desc()).limit(60)).scalars()) if ids else []
    cov = coverage_table(session, run, cres.bottom)
    cov = cov[_mask(cov, oem, region, product)]
    covd = [dict(month=str(r.month), oem=r.oem, region=r.region, product=r["product"], horizon=int(r.horizon), coverage=None if not np.isfinite(r.coverage) else float(r.coverage),
                 threshold=float(r.threshold), backlog_usd=float(r.bl_value), forecast_usd=float(r.consensus_revenue)) for _, r in cov.sort_values(["month"]).head(60).iterrows()]
    seg = session.execute(select(SeriesSegment).where(SeriesSegment.run_id == run.id, SeriesSegment.oem_code == oem, SeriesSegment.region_code == region,
                                                      SeriesSegment.product_code == product)).scalar_one_or_none()
    drivers = dict(segment=seg.segment if seg else None, adi=seg.adi if seg else None, cv2=seg.cv2 if seg else None, seasonality_strength=seg.seasonality_strength if seg else None,
                   acf12=seg.acf12 if seg else None, exog_strength=seg.exog_strength if seg else None, net_uplift_beta=(run.summary or {}).get("headline", {}).get("net_uplift_beta"),
                   commercial_model=(run.summary or {}).get("commercial_model"))
    return dict(explorer=ex, opportunities=opps, overrides=[override_out(o) for o in ovs], fva=[fva_out(f) for f in fva], audit=[audit_out(a) for a in audit], coverage=covd, drivers=drivers)


def override_out(o: Override) -> dict:
    return dict(id=o.id, run_id=o.run_id, level=o.level, oem=o.oem_code, region=o.region_code, product=o.product_code, month=o.month, ai_p50_forecast=o.ai_p50_forecast,
                ai_p50_units=o.ai_p50_units, override_basis=o.override_basis, sales_override_value=o.sales_override_value, override_units=o.override_units,
                override_revenue=o.override_revenue, consensus_value=o.consensus_value, reason_code=o.reason_code, comment=o.comment, user_id=o.user_id, timestamp=o.timestamp,
                revision=o.revision, status=o.status, approval_status=o.approval_status, is_synthetic=o.is_synthetic)


def fva_out(f: FvaResult) -> dict:
    return dict(run_id=f.run_id, scope=f.scope, horizon=f.horizon, n_obs=f.n_obs, wmape_naive=f.wmape_naive, wmape_ai=f.wmape_ai, wmape_consensus=f.wmape_consensus,
                fva_sales=f.fva_sales, fva_ai=f.fva_ai, fva_sales_ci_low=f.fva_sales_ci_low, fva_sales_ci_high=f.fva_sales_ci_high, significant=f.significant)


def audit_out(a: AuditLog) -> dict:
    return dict(id=a.id, ts=a.ts, user_id=a.user_id, action=a.action, entity_type=a.entity_type, entity_id=a.entity_id, before=a.before, after=a.after, hash=a.hash)


def dashboard(session: Session, run: ForecastRun) -> dict:
    cres = build_consensus(session, run.id)
    ex = explorer(session, run, ALL, ALL, ALL, cres)
    cb = cres.bottom
    ct = coverage_table(session, run, cb, with_thresholds=False)
    bl, fc3 = float(ct.bl_value.sum()), float(ct.consensus_revenue.sum())
    alerts = list(session.execute(select(RiskAlert).where(RiskAlert.run_id == run.id)).scalars())
    gap_supply = sum(a.financial_impact_usd for a in alerts if a.alert_type in ("REVENUE_GAP", "SUPPLY_BOTTLENECK") and a.status != "RESOLVED")
    pipe = sum(a.financial_impact_usd for a in alerts if a.alert_type == "PIPELINE_VULNERABILITY" and a.status != "RESOLVED")
    p90 = sum(f["revenue_p90"] for f in ex["forecast"])
    # realised accuracy of frozen forecasts on matured months
    cp = pd.DataFrame(session.execute(select(ConsensusPoint.run_id, ConsensusPoint.oem_code, ConsensusPoint.region_code, ConsensusPoint.product_code, ConsensusPoint.month,
                                             ConsensusPoint.horizon, ConsensusPoint.ai_units, ConsensusPoint.consensus_units, ConsensusPoint.asp_usd)).all(),
                      columns=["run", "oem", "region", "product", "month", "horizon", "ai", "cons", "asp"])
    act = pd.DataFrame(session.execute(select(MappedSeries.month, MappedSeries.oem_code, MappedSeries.region_code, MappedSeries.product_code, MappedSeries.units, MappedSeries.revenue_usd)
                                       .where(MappedSeries.oem_code != "UNMAPPED")).all(), columns=["month", "oem", "region", "product", "y", "yrev"])
    realized = None
    if len(cp) and len(act):
        j = cp.merge(act, on=["month", "oem", "region", "product"], how="inner")
        if len(j):
            j1 = j[j.horizon == 1]
            months = sorted(j1.month.unique())[-3:]
            j1 = j1[j1.month.isin(months)]
            ai_rev, act_rev = float((j1.ai * j1.asp).sum()), float(j1.yrev.sum())
            realized = dict(months=[str(m) for m in months], ai_revenue=ai_rev, actual_revenue=act_rev, variance_pct=(ai_rev / act_rev - 1) if act_rev else None,
                            ai_wmape=float((j.ai - j.y).abs().sum() / max(j.y.abs().sum(), 1e-9)), consensus_wmape=float((j.cons - j.y).abs().sum() / max(j.y.abs().sum(), 1e-9)), n=int(len(j)))
    top = sorted(alerts, key=lambda a: -a.financial_impact_usd)[:5]
    cyc = session.execute(select(PlanningCycle).where(PlanningCycle.cycle_month == run.cycle_month)).scalar_one_or_none()
    n_ov = session.execute(select(func.count()).select_from(Override).where(Override.run_id == run.id, Override.status == "ACTIVE")).scalar()
    return dict(run_id=run.id, cycle_month=run.cycle_month, cycle_status=cyc.status if cyc else None, locked=run.locked_at is not None, synthetic=run.is_synthetic,
                kpis=dict(total_consensus_revenue=ex["totals"]["consensus_revenue"], total_ai_revenue=ex["totals"]["ai_revenue"], consensus_vs_ai_pct=(ex["totals"]["consensus_revenue"] / ex["totals"]["ai_revenue"] - 1) if ex["totals"]["ai_revenue"] else None,
                          backlog_coverage=bl / fc3 if (fc3 and has_rows(session, BacklogSnapshot)) else None, backlog_value_t3=bl, forecast_value_t3=fc3, revenue_at_risk=gap_supply, pipeline_at_risk=pipe,
                          upside_potential=max(p90 - ex["totals"]["consensus_revenue"], 0.0), overall_wmape_realized=realized["ai_wmape"] if realized else None,
                          backtest_wmape=(run.summary or {}).get("headline", {}).get("champion_backtest_wmape"), active_overrides=int(n_ov), net_uplift_revenue=ex["totals"]["uplift_revenue"]),
                ai_vs_actual=realized, trend=dict(history=ex["history"][-24:], forecast=ex["forecast"]),
                top_risks=[dict(id=a.id, type=a.alert_type, severity=a.severity, title=a.title, impact=a.financial_impact_usd, oem=a.oem_code, region=a.region_code, product=a.product_code) for a in top],
                headline=(run.summary or {}).get("headline"), segments=(run.summary or {}).get("segments"))
