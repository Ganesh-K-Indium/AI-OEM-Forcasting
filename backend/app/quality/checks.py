"""Data-quality gate. ERROR-severity failures block a forecast run (configurable); everything is persisted for the Admin UI."""
from __future__ import annotations

import uuid
from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.calendar import add_months
from app.core.settings_store import get_setting
from app.models.facts import BacklogSnapshot, CapacityAllocation, MappedSeries, OpportunitySnapshot, SalesActual
from app.models.ops import DqResult
from app.models.reference import Account, AccountOemMapping, FxRate


def _r(name, sev, passed, msg, metric=None, threshold=None, detail=None) -> dict:
    return dict(check_name=name, severity=sev, passed=bool(passed), message=msg, metric=None if metric is None else float(metric),
                threshold=None if threshold is None else float(threshold), detail=detail)


def run_dq(session: Session, cycle_month: date, horizon: int = 12, persist: bool = True) -> list[dict]:
    res: list[dict] = []
    ms = pd.DataFrame(session.execute(select(MappedSeries.month, MappedSeries.oem_code, MappedSeries.region_code, MappedSeries.product_code,
                                             MappedSeries.units, MappedSeries.revenue_usd).where(MappedSeries.month <= cycle_month)).all(),
                      columns=["month", "oem", "region", "product", "units", "rev"])
    if ms.empty:
        res.append(_r("mapped_series_present", "ERROR", False, "No mapped history - run the mapping pipeline / materialisation first"))
        return _persist(session, res, persist)
    tot = ms.units.sum()
    unm = ms[ms.oem == "UNMAPPED"].units.sum()
    share = unm / tot if tot else 0
    res.append(_r("unmapped_share", "ERROR" if share > 0.10 else "WARN", share <= 0.03, f"{share:.1%} of units are not mapped to an OEM (warn >3%, error >10%)", share, 0.03))
    pending = session.execute(select(func.count()).select_from(AccountOemMapping).where(AccountOemMapping.status == "PENDING_REVIEW")).scalar()
    res.append(_r("pending_mapping_reviews", "INFO", pending == 0, f"{pending} mapping suggestions await steward review", pending))
    # distributor allocations
    alloc = pd.DataFrame(session.execute(select(AccountOemMapping.account_id, func.sum(AccountOemMapping.allocation_pct)).where(
        AccountOemMapping.status == "ACTIVE", AccountOemMapping.valid_to.is_(None)).group_by(AccountOemMapping.account_id)).all(), columns=["a", "s"])
    bad = alloc[(alloc.s - 1).abs() > 1e-4]
    res.append(_r("allocation_sums", "ERROR", bad.empty, f"{len(bad)} accounts have open allocations that do not sum to 100%", len(bad), 0, {"accounts": bad.a.tolist()[:20]}))
    m = ms[ms.oem != "UNMAPPED"]
    months = sorted(m.month.unique())
    res.append(_r("history_length", "ERROR" if len(months) < 18 else "WARN", len(months) >= 30, f"{len(months)} months of history (need >=18, recommend >=30)", len(months), 30))
    last = m[m.month == months[-1]].units.sum()
    prev = m[m.month.isin(months[-4:-1])].groupby("month").units.sum().mean()
    ratio = last / prev if prev else np.nan
    res.append(_r("latest_month_complete", "WARN", np.isfinite(ratio) and 0.5 <= ratio <= 1.6, f"latest month volume is {ratio:.0%} of the trailing-3-month average (possible partial load)", ratio, 0.5))
    neg = int((session.execute(select(func.count()).select_from(SalesActual).where((SalesActual.units < 0) | (SalesActual.revenue_local < 0))).scalar()))
    res.append(_r("negative_values", "WARN", neg == 0, f"{neg} ERP rows with negative units/revenue (returns/credit notes should be netted upstream)", neg, 0))
    # FX coverage
    ccys = {c for (c,) in session.execute(select(SalesActual.currency).distinct())} - {"USD"}
    miss = 0
    for c in ccys:
        have = {mo for (mo,) in session.execute(select(FxRate.month).where(FxRate.currency == c))}
        miss += len([mo for mo in months if pd.Timestamp(mo).date() not in have and mo not in have])
    res.append(_r("fx_coverage", "ERROR", miss == 0, f"{miss} missing currency-month FX rates", miss, 0))
    # ASP outliers
    asp = (m.rev / m.units.where(m.units > 0)).dropna()
    med = asp.groupby(m["product"].reindex(asp.index)).transform("median")
    out = int((np.abs(np.log((asp / med).clip(1e-6))) > 1.0).sum())
    res.append(_r("asp_outliers", "WARN", out == 0, f"{out} OEM-product-months have ASP more than 2.7x away from the product median", out, 0))
    # CRM / backlog / capacity freshness
    def total(model) -> int:
        return int(session.execute(select(func.count()).select_from(model)).scalar() or 0)

    def n_na(name, msg):
        res.append(_r(name, "INFO", True, msg))

    if total(OpportunitySnapshot) == 0:
        n_na("crm_snapshot_current", "no CRM data in this workspace - commercial uplift and pipeline risk are disabled")
    else:
        snap = session.execute(select(func.count()).select_from(OpportunitySnapshot).where(OpportunitySnapshot.snapshot_month == cycle_month)).scalar()
        res.append(_r("crm_snapshot_current", "WARN", snap > 0, f"{snap} opportunity snapshots for {cycle_month:%Y-%m} (commercial uplift needs the current snapshot)", snap, 1))
    if total(BacklogSnapshot) == 0:
        n_na("backlog_snapshot_current", "no backlog data in this workspace - coverage / revenue-gap alerts are disabled")
    else:
        bl = session.execute(select(func.count()).select_from(BacklogSnapshot).where(BacklogSnapshot.snapshot_month == cycle_month)).scalar()
        res.append(_r("backlog_snapshot_current", "WARN", bl > 0, f"{bl} backlog rows for snapshot {cycle_month:%Y-%m}", bl, 1))
    if total(CapacityAllocation) == 0:
        n_na("capacity_coverage", "no capacity data in this workspace - supply-bottleneck alerts are disabled")
    else:
        want = {add_months(cycle_month, i) for i in range(1, horizon + 1)}
        have = {mo for (mo,) in session.execute(select(CapacityAllocation.month).distinct())}
        res.append(_r("capacity_coverage", "WARN", want <= have, f"capacity allocation defined for {len(want & have)}/{horizon} forecast months", len(want & have), horizon))
    return _persist(session, res, persist)


def _persist(session: Session, res: list[dict], persist: bool) -> list[dict]:
    if persist:
        batch = str(uuid.uuid4())
        for r in res:
            session.add(DqResult(batch_id=batch, **r))
        session.flush()
    return res


def blocking_errors(session: Session, res: list[dict]) -> list[dict]:
    if not get_setting(session, "dq_block_on_error"):
        return []
    return [r for r in res if r["severity"] == "ERROR" and not r["passed"]]
