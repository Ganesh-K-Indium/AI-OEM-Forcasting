from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.aio import UserCtx, in_session, in_thread
from app.api.deps import run_or_404
from app.core.audit import verify_chain
from app.core.db import get_adb
from app.core.security import current_user, require_roles
from app.forecasting.service import audit_out, fva_out, override_out
from app.governance import cycles, fva, overrides
from app.governance.consensus import build_consensus
from app.models.governance import AuditLog, FvaResult, Override, PlanningCycle
from app.models.ops import User
from app.risk.engine import refresh_alerts
from app.schemas.forecast import AuditOut, FvaOut, OverrideIn, OverrideOut, ReviewIn

router = APIRouter(tags=["governance"], dependencies=[Depends(current_user)])


def _guard(fn):
    """Map domain errors to HTTP codes."""
    def wrapped(*a, **kw):
        try:
            return fn(*a, **kw)
        except overrides.ScopeError as e:
            raise HTTPException(403, str(e)) from e
        except (overrides.OverrideError, cycles.CycleError) as e:
            raise HTTPException(409 if isinstance(e, cycles.CycleError) else 422, str(e)) from e
    return wrapped


@_guard
def _create(s, ctx: UserCtx, body: OverrideIn):
    ov = overrides.create_override(s, ctx, body.run_id, body.oem, body.region, body.product, body.month, body.basis, body.value, body.reason_code, body.comment)
    refresh_alerts(s, body.run_id)
    return override_out(ov)


@router.post("/overrides", response_model=OverrideOut, status_code=201)
async def create_override(body: OverrideIn, user: User = Depends(require_roles("planner", "sales_rep"))):
    return await in_thread(_create, UserCtx.of(user), body, commit=True)


@router.get("/overrides", response_model=list[OverrideOut])
async def list_overrides(run_id: str | None = None, status: str | None = None, limit: int = 200, db: AsyncSession = Depends(get_adb)):
    def q(s):
        run = run_or_404(s, run_id)
        stmt = select(Override).where(Override.run_id == run.id).order_by(Override.timestamp.desc(), Override.id.desc()).limit(limit)
        if status:
            stmt = stmt.where(Override.status == status)
        return [override_out(o) for o in s.execute(stmt).scalars()]
    return await in_session(db, q)


@_guard
def _withdraw(s, ctx: UserCtx, override_id: int):
    ov = overrides.withdraw_override(s, ctx, override_id)
    refresh_alerts(s, ov.run_id)
    return override_out(ov)


@router.delete("/overrides/{override_id}", response_model=OverrideOut)
async def withdraw(override_id: int, user: User = Depends(require_roles("planner", "sales_rep"))):
    return await in_thread(_withdraw, UserCtx.of(user), override_id, commit=True)


@_guard
def _review(s, ctx: UserCtx, override_id: int, body: ReviewIn):
    ov = overrides.review_override(s, ctx, override_id, body.approve, body.note)
    refresh_alerts(s, ov.run_id)
    return override_out(ov)


@router.post("/overrides/{override_id}/review", response_model=OverrideOut)
async def review(override_id: int, body: ReviewIn, user: User = Depends(require_roles("planner"))):
    return await in_thread(_review, UserCtx.of(user), override_id, body, commit=True)


@router.get("/consensus/conflicts")
async def conflicts(run_id: str | None = None):
    return await in_thread(lambda s: build_consensus(s, run_or_404(s, run_id).id).conflicts)


@router.get("/cycles")
async def list_cycles(db: AsyncSession = Depends(get_adb)):
    rows = (await db.execute(select(PlanningCycle).order_by(PlanningCycle.cycle_month.desc()))).scalars()
    return [dict(id=c.id, cycle_month=c.cycle_month, status=c.status, run_id=c.run_id, lock_date=c.lock_date, locked_at=c.locked_at) for c in rows]


@_guard
def _lock(s, email: str, run_id: str):
    n = cycles.lock_run(s, run_id, email)
    fva.compute_fva(s)
    return {"locked": True, "consensus_rows": n}


@router.post("/runs/{run_id}/lock")
async def lock(run_id: str, user: User = Depends(require_roles("planner"))):
    return await in_thread(_lock, user.email, run_id, commit=True)


@_guard
def _consensus(s, email: str, cycle_month: date):
    return {"status": cycles.advance_to_consensus(s, cycle_month, email).status}


@router.post("/cycles/{cycle_month}/consensus")
async def open_consensus(cycle_month: date, user: User = Depends(require_roles("planner"))):
    return await in_thread(_consensus, user.email, cycle_month, commit=True)


@router.get("/fva", response_model=list[FvaOut])
async def get_fva(scope: str | None = None, db: AsyncSession = Depends(get_adb)):
    q = select(FvaResult).where(FvaResult.run_id == "ALL")
    if scope:
        q = q.where(FvaResult.scope == scope)
    return [fva_out(f) for f in (await db.execute(q.order_by(FvaResult.scope, FvaResult.horizon))).scalars()]


@router.get("/fva/by-run", response_model=list[FvaOut])
async def fva_by_run(db: AsyncSession = Depends(get_adb)):
    return [fva_out(f) for f in (await db.execute(select(FvaResult).where(FvaResult.scope == "RUN"))).scalars()]


@router.post("/fva/refresh")
async def refresh_fva(user: User = Depends(require_roles("planner"))):
    return {"rows": await in_thread(lambda s: fva.compute_fva(s), commit=True)}


@router.get("/audit", response_model=list[AuditOut])
async def audit(entity_type: str | None = None, action: str | None = None, user_id: str | None = None, limit: int = 200, db: AsyncSession = Depends(get_adb)):
    q = select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 1000))
    for col, val in ((AuditLog.entity_type, entity_type), (AuditLog.action, action), (AuditLog.user_id, user_id)):
        if val:
            q = q.where(col == val)
    return [audit_out(a) for a in (await db.execute(q)).scalars()]


@router.get("/audit/verify")
async def audit_verify(db: AsyncSession = Depends(get_adb)):
    ok, bad = await db.run_sync(verify_chain)
    return {"intact": ok, "first_bad_id": bad}
