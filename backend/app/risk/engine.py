"""Tier 5 - Backlog coverage & risk engine (post-consensus).

 Coverage Ratio = Confirmed Backlog / Consensus Forecast  (per OEM x Region x Product x delivery month, T+1..T+3)
 1. REVENUE_GAP            coverage < threshold  ->  uncovered revenue = Forecast - Backlog
                           threshold = configured floor, relaxed to the product's own historical P10 coverage at that lead time
                           (short-lead products legitimately carry little backlog)
 2. SUPPLY_BOTTLENECK      consensus units (region x product, all OEMs) > factory capacity allocation
 3. PIPELINE_VULNERABILITY one unclosed early-stage opportunity carries > X% of the (net) commercial uplift of a series."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.core.calendar import add_months
from app.governance.consensus import build_consensus
from app.mapping.service import load_mapping_table, map_fact_frame, to_usd
from app.models.facts import BacklogSnapshot, CapacityAllocation, MappedSeries
from app.models.forecast import ForecastPoint, ForecastRun, UpliftDetail
from app.models.governance import RiskAlert, RiskThreshold

HIGH_USD, MED_USD = 1_000_000.0, 250_000.0
COVERAGE_HORIZON = 3


@dataclass
class Thr:
    min_coverage: float = 0.60
    use_hist: bool = True
    concentration: float = 0.70
    max_stage: int = 3
    min_uplift_share: float = 0.05


def seed_default_thresholds(session: Session) -> None:
    if session.execute(select(RiskThreshold.id).limit(1)).first() is None:
        session.add(RiskThreshold(product_code=None, region_code=None))
        session.flush()


def _thr_table(session: Session) -> list[RiskThreshold]:
    seed_default_thresholds(session)
    return list(session.execute(select(RiskThreshold)).scalars())


def threshold_for(rows: list[RiskThreshold], product: str, region: str) -> Thr:
    for p, r in ((product, region), (product, None), (None, region), (None, None)):
        for t in rows:
            if t.product_code == p and t.region_code == r:
                return Thr(t.min_coverage, t.use_historical_baseline, t.concentration_threshold, t.concentration_max_stage, t.min_uplift_share)
    return Thr()


def _sev(impact: float) -> str:
    return "HIGH" if impact >= HIGH_USD else "MEDIUM" if impact >= MED_USD else "LOW"


def backlog_bottom(session: Session, snapshot: "date", mapping: pd.DataFrame | None = None) -> pd.DataFrame:
    rows = session.execute(select(BacklogSnapshot.snapshot_month, BacklogSnapshot.delivery_month, BacklogSnapshot.account_id, BacklogSnapshot.end_account_id,
                                  BacklogSnapshot.product_code, BacklogSnapshot.units, BacklogSnapshot.value_local, BacklogSnapshot.currency)
                           .where(BacklogSnapshot.snapshot_month == snapshot)).all()
    df = pd.DataFrame(rows, columns=["snapshot_month", "delivery_month", "account_id", "end_account_id", "product_code", "units", "value_local", "currency"])
    return _map_backlog(session, df, mapping, "snapshot_month")


def _map_backlog(session: Session, df: pd.DataFrame, mapping, month_col: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["oem", "region", "product", "month", "units", "value_usd"])
    mapping = load_mapping_table(session) if mapping is None else mapping
    m = map_fact_frame(df, mapping, ["units", "value_local"], month_col=month_col)
    m["value_usd"] = to_usd(m.rename(columns={"delivery_month": "dm"}), "value_local", session, month_col="dm", ccy_col="currency")
    m = m[m.oem_code != "UNMAPPED"]
    g = m.groupby(["oem_code", "region_code", "product_code", "delivery_month"], as_index=False)[["units", "value_usd"]].sum()
    return g.rename(columns={"oem_code": "oem", "region_code": "region", "product_code": "product", "delivery_month": "month"})


def historical_coverage(session: Session, cycle_month, depth: int = 17) -> pd.DataFrame:
    """Past coverage by (product, horizon): backlog units at snapshot c' for delivery c'+h  /  units actually shipped. -> p10 per product x h."""
    snaps = [add_months(cycle_month, -i) for i in range(1, depth + 1)]
    rows = session.execute(select(BacklogSnapshot.snapshot_month, BacklogSnapshot.delivery_month, BacklogSnapshot.account_id, BacklogSnapshot.end_account_id,
                                  BacklogSnapshot.product_code, BacklogSnapshot.units, BacklogSnapshot.value_local, BacklogSnapshot.currency)
                           .where(BacklogSnapshot.snapshot_month.in_(snaps), BacklogSnapshot.delivery_month <= cycle_month)).all()
    df = pd.DataFrame(rows, columns=["snapshot_month", "delivery_month", "account_id", "end_account_id", "product_code", "units", "value_local", "currency"])
    if df.empty:
        return pd.DataFrame(columns=["product", "h", "p10", "median", "n"])
    mapping = load_mapping_table(session)
    parts = []
    for snap, g in df.groupby("snapshot_month"):
        b = _map_backlog(session, g, mapping, "snapshot_month")
        b["snapshot_month"] = snap
        parts.append(b)
    bl = pd.concat(parts)
    act = pd.DataFrame(session.execute(select(MappedSeries.month, MappedSeries.oem_code, MappedSeries.region_code, MappedSeries.product_code, MappedSeries.units)
                                       .where(MappedSeries.oem_code != "UNMAPPED")).all(), columns=["month", "oem", "region", "product", "y"])
    j = bl.merge(act, on=["month", "oem", "region", "product"])
    j = j[j.y > 0]
    j["h"] = ((pd.to_datetime(j.month).dt.year - pd.to_datetime(j.snapshot_month).dt.year) * 12 + pd.to_datetime(j.month).dt.month - pd.to_datetime(j.snapshot_month).dt.month)
    j["cov"] = (j.units / j.y).clip(0, 1.5)
    j = j[(j.h >= 1) & (j.h <= COVERAGE_HORIZON)]
    return j.groupby(["product", "h"])["cov"].agg(p10=lambda s: float(np.quantile(s, 0.10)), median="median", n="size").reset_index()


def coverage_table(session: Session, run: ForecastRun, cons_bottom: pd.DataFrame | None = None, with_thresholds: bool = True) -> pd.DataFrame:
    """Per bottom x month (T+1..T+3): consensus units/revenue, backlog units/value, coverage and effective threshold."""
    cons = build_consensus(session, run.id).bottom if cons_bottom is None else cons_bottom
    cons = cons[cons.horizon <= COVERAGE_HORIZON].copy()
    bl = backlog_bottom(session, run.cycle_month)
    t = cons.merge(bl.rename(columns={"units": "bl_units", "value_usd": "bl_value"}), on=["oem", "region", "product", "month"], how="left").fillna({"bl_units": 0.0, "bl_value": 0.0})
    t["coverage"] = np.where(t.consensus_units > 1e-9, t.bl_units / t.consensus_units, np.nan)
    if with_thresholds:
        thr_rows, hist = _thr_table(session), historical_coverage(session, run.cycle_month)
        hp = {(r["product"], int(r["h"])): r["p10"] for r in hist.to_dict("records")} if len(hist) else {}
        eff, floor = [], []
        for r in t.itertuples(index=False):
            th = threshold_for(thr_rows, r.product, r.region)
            e = th.min_coverage
            if th.use_hist and (r.product, int(r.horizon)) in hp:
                e = min(e, hp[(r.product, int(r.horizon))])
            eff.append(e)
            floor.append(th.min_coverage)
        t["threshold"], t["configured_floor"] = eff, floor
    return t


def refresh_alerts(session: Session, run_id: str) -> int:
    run = session.get(ForecastRun, run_id)
    cons = build_consensus(session, run_id).bottom
    alerts: list[dict] = []
    # ---------------------------------------------------------------- 1. revenue gap
    ct = coverage_table(session, run, cons)
    for (o, r, p), g in ct.groupby(["oem", "region", "product"]):
        bad = g[(g.coverage < g.threshold) & (g.consensus_revenue > 0)].sort_values("month")
        if bad.empty:
            continue
        gap = float((bad.consensus_revenue - bad.bl_value).clip(lower=0).sum())
        if gap <= 0:
            continue
        alerts.append(dict(run_id=run_id, alert_type="REVENUE_GAP", severity=_sev(gap), oem_code=o, region_code=r, product_code=p, first_month=bad.month.min(),
                           last_month=bad.month.max(), financial_impact_usd=gap,
                           title=f"Revenue gap risk: {o} {r} {p} - coverage {bad.coverage.min():.0%} < {bad.threshold.iloc[0]:.0%} in T+{int(bad.horizon.min())}..T+{int(bad.horizon.max())}",
                           detail=dict(months=[dict(month=str(x.month), horizon=int(x.horizon), coverage=float(x.coverage), threshold=float(x.threshold),
                                                    configured_floor=float(x.configured_floor), forecast_usd=float(x.consensus_revenue), backlog_usd=float(x.bl_value),
                                                    uncovered_usd=float(max(x.consensus_revenue - x.bl_value, 0))) for x in bad.itertuples()])))
    # ---------------------------------------------------------------- 2. supply bottleneck
    cap = pd.DataFrame(session.execute(select(CapacityAllocation.month, CapacityAllocation.region_code, CapacityAllocation.product_code, CapacityAllocation.capacity_units)).all(),
                       columns=["month", "region", "product", "capacity"])
    rp = cons.groupby(["region", "product", "month"], as_index=False).agg(units=("consensus_units", "sum"), rev=("consensus_revenue", "sum"))
    rp = rp.merge(cap, on=["month", "region", "product"], how="inner")
    rp["excess"] = (rp.units - rp.capacity).clip(lower=0)
    rp["asp"] = np.where(rp.units > 0, rp.rev / rp.units, 0.0)
    for (r, p), g in rp[rp.excess > 1e-6].groupby(["region", "product"]):
        impact = float((g.excess * g.asp).sum())
        top = cons[(cons.region == r) & (cons["product"] == p) & (cons.month.isin(g.month))].groupby("oem").consensus_units.sum().sort_values(ascending=False)
        alerts.append(dict(run_id=run_id, alert_type="SUPPLY_BOTTLENECK", severity=_sev(impact), oem_code="ALL", region_code=r, product_code=p, first_month=g.month.min(),
                           last_month=g.month.max(), financial_impact_usd=impact,
                           title=f"Supply bottleneck: {r} {p} demand exceeds capacity in {len(g)} month(s) (peak +{g.excess.max():,.0f} kunits)",
                           detail=dict(months=[dict(month=str(x.month), consensus_units=float(x.units), capacity_units=float(x.capacity), excess_units=float(x.excess),
                                                    revenue_at_risk_usd=float(x.excess * x.asp)) for x in g.itertuples()],
                                       top_oems={k: float(v) for k, v in top.head(5).items()})))
    # ---------------------------------------------------------------- 3. pipeline vulnerability
    fp = pd.DataFrame(session.execute(select(ForecastPoint.oem_code, ForecastPoint.region_code, ForecastPoint.product_code, ForecastPoint.month, ForecastPoint.units_p50,
                                             ForecastPoint.uplift_units, ForecastPoint.gross_uplift_units, ForecastPoint.asp_usd)
                                      .where(ForecastPoint.run_id == run_id, ForecastPoint.level == "BOTTOM")).all(),
                      columns=["oem", "region", "product", "month", "units", "uplift", "gross", "asp"])
    ud = pd.DataFrame(session.execute(select(UpliftDetail.oem_code, UpliftDetail.region_code, UpliftDetail.product_code, UpliftDetail.opportunity_id, UpliftDetail.sfdc_id,
                                             UpliftDetail.stage, UpliftDetail.expected_units).where(UpliftDetail.run_id == run_id)).all(),
                      columns=["oem", "region", "product", "opp", "sfdc", "stage", "exp"])
    thr_rows = _thr_table(session)
    if len(ud):
        for (o, r, p), g in ud.groupby(["oem", "region", "product"]):
            f = fp[(fp.oem == o) & (fp.region == r) & (fp["product"] == p)]
            net, gross, units = f.uplift.sum(), f.gross.sum(), f.units.sum()
            if gross <= 1e-9 or net <= 1e-9 or units <= 0:
                continue
            th = threshold_for(thr_rows, p, r)
            per = g.groupby(["opp", "sfdc", "stage"], as_index=False).exp.sum().sort_values("exp", ascending=False)
            top = per.iloc[0]
            share = float(top.exp / per.exp.sum())
            if share >= th.concentration and int(top.stage) <= th.max_stage and net / units >= th.min_uplift_share:
                top_net = float(top.exp * net / gross)
                impact = top_net * float((f.asp * f.units).sum() / max(f.units.sum(), 1e-9))
                alerts.append(dict(run_id=run_id, alert_type="PIPELINE_VULNERABILITY", severity=_sev(impact), oem_code=o, region_code=r, product_code=p,
                                   first_month=f.month.min(), last_month=f.month.max(), financial_impact_usd=impact,
                                   title=f"Pipeline vulnerability: {share:.0%} of {o} {r} {p} uplift rides on one Stage-{int(top.stage)} opportunity ({top.sfdc})",
                                   detail=dict(opportunity_id=int(top.opp), sfdc_id=top.sfdc, stage=int(top.stage), top1_share=share, uplift_share_of_forecast=float(net / units),
                                               net_uplift_units=float(net), n_opportunities=int(len(per)))))
    session.execute(delete(RiskAlert).where(RiskAlert.run_id == run_id))
    if alerts:
        session.execute(insert(RiskAlert), alerts)
    session.flush()
    return len(alerts)
