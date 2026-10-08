from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.aio import UserCtx, in_session, in_thread
from app.api.deps import run_or_404
from app.core.audit import audit
from app.core.db import get_adb
from app.core.security import current_user, require_roles
from app.governance.consensus import build_consensus
from app.models.governance import RiskAlert, RiskThreshold
from app.models.ops import User
from app.risk.engine import coverage_table, refresh_alerts, seed_default_thresholds
from app.schemas.risk import AlertOut, AlertPatch, RiskSummary, ThresholdIn, ThresholdOut

router = APIRouter(prefix="/risk", tags=["risk"], dependencies=[Depends(current_user)])


def _alert(a: RiskAlert) -> AlertOut:
    return AlertOut(id=a.id, run_id=a.run_id, alert_type=a.alert_type, severity=a.severity, oem=a.oem_code, region=a.region_code, product=a.product_code, first_month=a.first_month,
                    last_month=a.last_month, financial_impact_usd=a.financial_impact_usd, title=a.title, detail=a.detail, status=a.status, owner=a.owner, note=a.note)


def _alerts(s, run_id, alert_type, status, severity, oem, region):
    run = run_or_404(s, run_id)
    q = select(RiskAlert).where(RiskAlert.run_id == run.id).order_by(RiskAlert.financial_impact_usd.desc())
    for col, val in ((RiskAlert.alert_type, alert_type), (RiskAlert.status, status), (RiskAlert.severity, severity), (RiskAlert.oem_code, oem), (RiskAlert.region_code, region)):
        if val:
            q = q.where(col == val)
    return [_alert(a) for a in s.execute(q).scalars()]


@router.get("/alerts", response_model=list[AlertOut])
async def alerts(run_id: str | None = None, alert_type: str | None = None, status: str | None = None, severity: str | None = None, oem: str | None = None,
                 region: str | None = None, db: AsyncSession = Depends(get_adb)):
    return await in_session(db, _alerts, run_id, alert_type, status, severity, oem, region)


def _patch(s, email: str, alert_id: int, body: AlertPatch):
    a = s.get(RiskAlert, alert_id)
    if a is None:
        raise HTTPException(404, "alert not found")
    before = dict(status=a.status, owner=a.owner, note=a.note)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(a, k, v)
    audit(s, email, "RISK_ALERT_UPDATE", "risk_alert", a.id, before, body.model_dump(exclude_unset=True))
    return _alert(a)


@router.patch("/alerts/{alert_id}", response_model=AlertOut)
async def patch_alert(alert_id: int, body: AlertPatch, db: AsyncSession = Depends(get_adb), user: User = Depends(require_roles("planner", "sales_rep"))):
    return await in_session(db, _patch, user.email, alert_id, body, commit=True)


def _summary(s, run_id):
    run = run_or_404(s, run_id)
    al = [a for a in s.execute(select(RiskAlert).where(RiskAlert.run_id == run.id)).scalars() if a.status != "RESOLVED"]
    by: dict[str, dict] = {}
    oems: dict[str, float] = {}
    for a in al:
        d = by.setdefault(a.alert_type, dict(count=0, impact_usd=0.0))
        d["count"] += 1
        d["impact_usd"] += a.financial_impact_usd
        oems[a.oem_code] = oems.get(a.oem_code, 0.0) + a.financial_impact_usd
    return RiskSummary(run_id=run.id, open_alerts=len(al), by_type=by, revenue_at_risk_usd=sum(a.financial_impact_usd for a in al if a.alert_type != "PIPELINE_VULNERABILITY"),
                       top_oems=[dict(oem=k, impact_usd=v) for k, v in sorted(oems.items(), key=lambda kv: -kv[1])[:6]])


@router.get("/summary", response_model=RiskSummary)
async def summary(run_id: str | None = None, db: AsyncSession = Depends(get_adb)):
    return await in_session(db, _summary, run_id)


def _coverage(s, run_id, oem, region):
    run = run_or_404(s, run_id)
    ct = coverage_table(s, run, build_consensus(s, run.id).bottom)
    if oem:
        ct = ct[ct.oem == oem]
    if region:
        ct = ct[ct.region == region]
    return [dict(oem=r.oem, region=r.region, product=r["product"], month=str(r.month), horizon=int(r.horizon), forecast_usd=float(r.consensus_revenue), backlog_usd=float(r.bl_value),
                 coverage=None if r.coverage != r.coverage else float(r.coverage), threshold=float(r.threshold), configured_floor=float(r.configured_floor)) for _, r in ct.iterrows()]


@router.get("/coverage")
async def coverage(run_id: str | None = None, oem: str | None = None, region: str | None = None):
    return await in_thread(_coverage, run_id, oem, region)


def _refresh(s, run_id):
    return {"alerts": refresh_alerts(s, run_or_404(s, run_id).id)}


@router.post("/refresh")
async def refresh(run_id: str | None = None, user: User = Depends(require_roles("planner"))):
    return await in_thread(_refresh, run_id, commit=True)


def _thr_out(t: RiskThreshold) -> ThresholdOut:
    return ThresholdOut(id=t.id, product_code=t.product_code, region_code=t.region_code, min_coverage=t.min_coverage, use_historical_baseline=t.use_historical_baseline,
                        concentration_threshold=t.concentration_threshold, concentration_max_stage=t.concentration_max_stage, min_uplift_share=t.min_uplift_share)


def _thresholds(s):
    seed_default_thresholds(s)
    return [_thr_out(t) for t in s.execute(select(RiskThreshold).order_by(RiskThreshold.id)).scalars()]


@router.get("/thresholds", response_model=list[ThresholdOut])
async def thresholds(db: AsyncSession = Depends(get_adb)):
    return await in_session(db, _thresholds, commit=True)


def _upsert(s, email: str, body: ThresholdIn):
    pc, rc = body.product_code, body.region_code
    t = s.execute(select(RiskThreshold).where(RiskThreshold.product_code.is_(None) if pc is None else RiskThreshold.product_code == pc,
                                              RiskThreshold.region_code.is_(None) if rc is None else RiskThreshold.region_code == rc)).scalar_one_or_none()
    before = None
    if t is None:
        t = RiskThreshold(product_code=pc, region_code=rc)
        s.add(t)
    else:
        before = dict(min_coverage=t.min_coverage, concentration_threshold=t.concentration_threshold)
    for k, v in body.model_dump().items():
        setattr(t, k, v)
    s.flush()
    audit(s, email, "RISK_THRESHOLD_SET", "risk_threshold", t.id, before, body.model_dump())
    return _thr_out(t)


@router.put("/thresholds", response_model=ThresholdOut)
async def upsert_threshold(body: ThresholdIn, db: AsyncSession = Depends(get_adb), user: User = Depends(require_roles("planner"))):
    return await in_session(db, _upsert, user.email, body, commit=True)
