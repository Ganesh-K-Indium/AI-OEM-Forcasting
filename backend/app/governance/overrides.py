"""Sales override service. The AI baseline is never mutated; every change is an append-only revision with a reason code."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.settings_store import get_setting
from app.ml.hierarchy import ALL, level_of
from app.models.forecast import ForecastPoint, ForecastRun
from app.models.governance import REASON_CODES, Override
from app.models.ops import User


class OverrideError(ValueError):
    pass


class ScopeError(PermissionError):
    pass


def _check_scope(user: User, oem: str, region: str) -> None:
    if user.role != "sales_rep":
        return
    for dim, allowed, val in (("OEM", user.scope_oems, oem), ("region", user.scope_regions, region)):
        if allowed and val not in allowed:
            raise ScopeError(f"{dim} '{val}' is outside your assigned scope ({', '.join(allowed)})")


def ai_node_point(session: Session, run_id: str, oem: str, region: str, product: str, month: date) -> ForecastPoint:
    fp = session.execute(select(ForecastPoint).where(ForecastPoint.run_id == run_id, ForecastPoint.oem_code == oem, ForecastPoint.region_code == region,
                                                     ForecastPoint.product_code == product, ForecastPoint.month == month)).scalar_one_or_none()
    if fp is None:
        raise OverrideError("no AI forecast exists for that node/month in this run")
    return fp


def create_override(session: Session, user: User, run_id: str, oem: str, region: str, product: str, month: date, basis: str, value: float,
                    reason_code: str, comment: str | None = None, synthetic: bool = False, enforce_scope: bool = True) -> Override:
    run = session.get(ForecastRun, run_id)
    if run is None or run.status != "COMPLETED":
        raise OverrideError("unknown or incomplete forecast run")
    if run.locked_at is not None:
        raise OverrideError("this forecast version is locked; overrides are closed for the cycle")
    if level_of(oem, region, product) == "INVALID":
        raise OverrideError("node must be a valid hierarchy level (e.g. OEM, OEM x Region, Region, Product, Total or a single series)")
    if reason_code not in REASON_CODES:
        raise OverrideError(f"reason_code must be one of {', '.join(REASON_CODES)}")
    if basis not in ("UNITS", "REVENUE"):
        raise OverrideError("basis must be UNITS or REVENUE")
    if value < 0:
        raise OverrideError("override value cannot be negative")
    if enforce_scope:
        _check_scope(user, oem, region)
    fp = ai_node_point(session, run_id, oem, region, product, month)
    asp = fp.asp_usd if fp.asp_usd > 0 else (fp.revenue_p50 / fp.units_p50 if fp.units_p50 > 0 else 0.0)
    units = value if basis == "UNITS" else (value / asp if asp > 0 else 0.0)
    revenue = value * asp if basis == "UNITS" else value
    dev = abs(units / fp.units_p50 - 1) * 100 if fp.units_p50 > 1e-9 else (100.0 if units > 0 else 0.0)
    limit = float(get_setting(session, "override_max_deviation_pct"))
    if dev > limit + 1e-6 and len((comment or "").strip()) < 15 and not synthetic:
        raise OverrideError(f"override deviates {dev:.0f}% from the AI forecast (limit {limit:.0f}%): a justification comment of >= 15 characters is required")
    prev = session.execute(select(Override).where(Override.run_id == run_id, Override.oem_code == oem, Override.region_code == region,
                                                  Override.product_code == product, Override.month == month, Override.status == "ACTIVE")).scalar_one_or_none()
    rev_no = 1
    if prev is not None:
        prev.status = "SUPERSEDED"
        rev_no = prev.revision + 1
    ov = Override(run_id=run_id, level=level_of(oem, region, product), oem_code=oem, region_code=region, product_code=product, month=month,
                  ai_p50_forecast=fp.revenue_p50, ai_p50_units=fp.units_p50, override_basis=basis, sales_override_value=value, override_units=units,
                  override_revenue=revenue, consensus_value=revenue, reason_code=reason_code, comment=comment, user_id=user.email, revision=rev_no,
                  supersedes_id=prev.id if prev else None, status="ACTIVE",
                  approval_status="PENDING" if get_setting(session, "override_requires_approval") else "APPROVED", is_synthetic=synthetic)
    session.add(ov)
    session.flush()
    audit(session, user.email, "OVERRIDE_CREATE" if prev is None else "OVERRIDE_REVISE", "override", ov.id,
          None if prev is None else dict(id=prev.id, value=prev.sales_override_value, reason=prev.reason_code),
          dict(node=f"{oem}|{region}|{product}", month=str(month), value=value, basis=basis, reason=reason_code, ai_units=fp.units_p50, deviation_pct=round(dev, 1)))
    return ov


def withdraw_override(session: Session, user: User, override_id: int) -> Override:
    ov = session.get(Override, override_id)
    if ov is None or ov.status != "ACTIVE":
        raise OverrideError("override not found or not active")
    run = session.get(ForecastRun, ov.run_id)
    if run.locked_at is not None:
        raise OverrideError("cycle is locked")
    if user.role == "sales_rep" and ov.user_id != user.email:
        raise ScopeError("reps can only withdraw their own overrides")
    ov.status = "WITHDRAWN"
    audit(session, user.email, "OVERRIDE_WITHDRAW", "override", ov.id, dict(value=ov.sales_override_value), dict(status="WITHDRAWN"))
    return ov


def review_override(session: Session, user: User, override_id: int, approve: bool, note: str | None = None) -> Override:
    ov = session.get(Override, override_id)
    if ov is None or ov.status != "ACTIVE":
        raise OverrideError("override not found or not active")
    ov.approval_status = "APPROVED" if approve else "REJECTED"
    ov.approver_id, ov.approved_at = user.email, datetime.utcnow()
    audit(session, user.email, "OVERRIDE_APPROVE" if approve else "OVERRIDE_REJECT", "override", ov.id, None, dict(note=note))
    return ov
