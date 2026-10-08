"""Planning cycle & forecast versioning: OPEN -> FORECASTED -> CONSENSUS -> LOCKED. Locking freezes AI + consensus for FVA."""
from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd
from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.calendar import add_months
from app.governance.consensus import build_consensus
from app.models.facts import MappedSeries
from app.models.forecast import ForecastRun
from app.models.governance import ConsensusPoint, PlanningCycle


class CycleError(ValueError):
    pass


def get_or_create_cycle(session: Session, cycle_month: date) -> PlanningCycle:
    c = session.execute(select(PlanningCycle).where(PlanningCycle.cycle_month == cycle_month)).scalar_one_or_none()
    if c is None:
        c = PlanningCycle(cycle_month=cycle_month, status="OPEN", lock_date=add_months(cycle_month, 1))
        session.add(c)
        session.flush()
    return c


def attach_run(session: Session, run_id: str) -> PlanningCycle:
    run = session.get(ForecastRun, run_id)
    c = get_or_create_cycle(session, run.cycle_month)
    if c.status == "LOCKED":
        raise CycleError("cycle already locked")
    c.run_id, c.status = run_id, "FORECASTED"
    return c


def _seasonal_naive(session: Session, cycle_month: date, months: list[date]) -> dict[tuple, dict[date, float]]:
    df = pd.DataFrame(session.execute(select(MappedSeries.month, MappedSeries.oem_code, MappedSeries.region_code, MappedSeries.product_code,
                                             MappedSeries.units).where(MappedSeries.month <= cycle_month, MappedSeries.oem_code != "UNMAPPED")).all(),
                      columns=["month", "o", "r", "p", "u"])
    out: dict[tuple, dict[date, float]] = {}
    for (o, r, p), g in df.groupby(["o", "r", "p"]):
        s = g.set_index("month").u
        last = float(s.iloc[-1]) if len(s) else 0.0
        out[(o, r, p)] = {m: float(s.get(add_months(m, -12), last)) for m in months}
    return out


def lock_run(session: Session, run_id: str, user: str) -> int:
    run = session.get(ForecastRun, run_id)
    if run is None or run.status != "COMPLETED":
        raise CycleError("run not completed")
    if run.locked_at is not None:
        raise CycleError("already locked")
    res = build_consensus(session, run_id)
    b = res.bottom
    months = sorted(b.month.unique())
    naive = _seasonal_naive(session, run.cycle_month, months)
    session.execute(delete(ConsensusPoint).where(ConsensusPoint.run_id == run_id))
    rows = [dict(run_id=run_id, oem_code=r.oem, region_code=r.region, product_code=r["product"], month=r.month, horizon=int(r.horizon), ai_units=float(r.ai_units),
                 consensus_units=float(r.consensus_units), asp_usd=float(r.asp), naive_units=float(naive.get((r.oem, r.region, r["product"]), {}).get(r.month, 0.0)),
                 overridden=bool(r.overridden), reason_code=r.reason_code, user_id=r.user_id) for _, r in b.iterrows()]
    session.execute(insert(ConsensusPoint), rows)
    run.locked_at, run.locked_by = datetime.utcnow(), user
    c = session.execute(select(PlanningCycle).where(PlanningCycle.run_id == run_id)).scalar_one_or_none()
    if c:
        c.status, c.locked_at = "LOCKED", datetime.utcnow()
    session.flush()
    from app.risk.engine import refresh_alerts

    refresh_alerts(session, run_id)
    audit(session, user, "CYCLE_LOCK", "forecast_run", run_id, None, {"overridden_rows": int(b.overridden.sum()), "conflicts": len(res.conflicts)})
    return len(rows)


def advance_to_consensus(session: Session, cycle_month: date, user: str) -> PlanningCycle:
    c = get_or_create_cycle(session, cycle_month)
    if c.status not in ("FORECASTED", "CONSENSUS"):
        raise CycleError(f"cycle is {c.status}; need a completed forecast first")
    c.status = "CONSENSUS"
    audit(session, user, "CYCLE_CONSENSUS_OPEN", "planning_cycle", c.id, None, {"cycle": str(cycle_month)})
    return c
