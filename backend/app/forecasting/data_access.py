"""Builds model-ready panels from the operational DB (as-of a planning cycle; nothing after `cycle_month` leaks in)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.calendar import month_range
from app.mapping.service import UNMAPPED, load_mapping_table, map_fact_frame
from app.ml.commercial import CrmData
from app.ml.hierarchy import Hierarchy, aggregate_wide, build_hierarchy, node_id
from app.models.facts import MappedSeries, Opportunity, OpportunitySnapshot
from app.models.reference import FxRate, ProductLine, Region


@dataclass
class Panel:
    hier: Hierarchy
    months: pd.DatetimeIndex
    units_bottom: pd.DataFrame  # months x bottom ids
    rev_bottom: pd.DataFrame
    units_nodes: pd.DataFrame  # months x node ids
    rev_nodes: pd.DataFrame

    def long(self, which: str = "units") -> pd.DataFrame:
        w = self.units_nodes if which == "units" else self.rev_nodes
        d = w.reset_index().melt(id_vars="index", var_name="unique_id", value_name="y").rename(columns={"index": "ds"})
        return d[["unique_id", "ds", "y"]]


def load_panel(session: Session, cycle_month: date, universe: list[tuple[str, str, str]] | None = None) -> Panel:
    df = pd.DataFrame(session.execute(select(MappedSeries.month, MappedSeries.oem_code, MappedSeries.region_code, MappedSeries.product_code,
                                             MappedSeries.units, MappedSeries.revenue_usd).where(MappedSeries.month <= cycle_month,
                                                                                                  MappedSeries.oem_code != UNMAPPED[0])).all(),
                      columns=["month", "oem", "region", "product", "units", "rev"])
    if df.empty:
        raise ValueError("mapped_series is empty - run mapping + materialisation first")
    df["month"] = pd.to_datetime(df["month"])
    bottoms = universe or sorted({tuple(x) for x in df[["oem", "region", "product"]].drop_duplicates().itertuples(index=False)})
    hier = build_hierarchy(bottoms)
    months = pd.date_range(df.month.min(), pd.Timestamp(cycle_month), freq="MS")
    df["bid"] = df.oem + "|" + df.region + "|" + df["product"]
    cols = [node_id(*b) for b in hier.bottoms]
    u = df.pivot_table(index="month", columns="bid", values="units", aggfunc="sum").reindex(index=months, columns=cols).fillna(0.0)
    r = df.pivot_table(index="month", columns="bid", values="rev", aggfunc="sum").reindex(index=months, columns=cols).fillna(0.0)
    return Panel(hier, months, u, r, aggregate_wide(u, hier), aggregate_wide(r, hier))


def fx_wide(session: Session) -> pd.DataFrame:
    fx = pd.DataFrame(session.execute(select(FxRate.month, FxRate.currency, FxRate.rate_to_usd)).all(), columns=["month", "currency", "rate"])
    fx["month"] = pd.to_datetime(fx["month"])
    w = fx.pivot(index="month", columns="currency", values="rate").sort_index()
    w["USD"] = 1.0
    return w


def load_crm(session: Session, cycle_month: date) -> CrmData:
    opps = pd.DataFrame(session.execute(select(Opportunity.id, Opportunity.product_code, Opportunity.rep_id, Opportunity.account_id,
                                               Opportunity.end_account_id, Opportunity.created_month, Opportunity.ramp_months,
                                               Opportunity.quantity_units, Opportunity.first_delivery_month)).all(),
                        columns=["id", "product_code", "rep_id", "account_id", "end_account_id", "created_month", "ramp_months", "quantity_units", "first_delivery_month"])
    snaps = pd.DataFrame(session.execute(select(OpportunitySnapshot.opportunity_id, OpportunitySnapshot.snapshot_month, OpportunitySnapshot.stage,
                                                OpportunitySnapshot.quantity_units, OpportunitySnapshot.expected_close_month,
                                                OpportunitySnapshot.months_in_stage, OpportunitySnapshot.push_count,
                                                OpportunitySnapshot.rep_probability, OpportunitySnapshot.quote_issued)
                                         .where(OpportunitySnapshot.snapshot_month <= cycle_month)).all(),
                         columns=["opportunity_id", "snapshot_month", "stage", "quantity_units", "expected_close_month", "months_in_stage",
                                  "push_count", "rep_probability", "quote_issued"])
    for c in ("created_month", "first_delivery_month"):
        opps[c] = pd.to_datetime(opps[c])
    for c in ("snapshot_month", "expected_close_month"):
        snaps[c] = pd.to_datetime(snaps[c])
    mapping = load_mapping_table(session)
    o = opps[["id", "account_id", "end_account_id"]].rename(columns={"id": "opportunity_id"}).assign(month=pd.Timestamp(cycle_month), pct0=1.0)
    nm = map_fact_frame(o, mapping, ["pct0"])
    nm = nm[nm.oem_code != UNMAPPED[0]][["opportunity_id", "oem_code", "region_code", "pct"]]
    prods = [p for (p,) in session.execute(select(ProductLine.code).order_by(ProductLine.code))]
    regs = [r for (r,) in session.execute(select(Region.code).order_by(Region.code))]
    return CrmData(opps, snaps, nm, prods, regs)


def pipeline_drivers(crm: CrmData, months: pd.DatetimeIndex, hier: Hierarchy) -> dict[str, pd.DataFrame]:
    """Per node: monthly open-pipeline quantity and won quantity (point-in-time) used as exogenous-driver evidence."""
    if crm.snaps.empty:
        return {u: pd.DataFrame({"pipeline_open": 0.0, "won_qty": 0.0}, index=months) for u in hier.ids}
    sn = crm.snaps.merge(crm.node_map, on="opportunity_id")
    sn["bid"] = sn.oem_code + "|" + sn.region_code + "|" + sn.opportunity_id.map(crm.opps.set_index("id")["product_code"])
    sn["q"] = sn.quantity_units * sn.pct
    op = sn[sn.stage <= 5].pivot_table(index="snapshot_month", columns="bid", values="q", aggfunc="sum")
    won = sn[sn.stage == 6].pivot_table(index="snapshot_month", columns="bid", values="q", aggfunc="sum")
    cols = [node_id(*b) for b in hier.bottoms]
    op = op.reindex(index=months, columns=cols).fillna(0.0)
    won = won.reindex(index=months, columns=cols).fillna(0.0)
    opn, wn = aggregate_wide(op, hier), aggregate_wide(won, hier)
    return {u: pd.DataFrame({"pipeline_open": opn[u], "won_qty": wn[u]}) for u in hier.ids}
