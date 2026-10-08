from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, JsonType

REASON_CODES = ("PROJECT_DELAY", "NEW_WIN", "CAPACITY_CAP", "CUSTOMER_DIRECT_GUIDANCE")


class Override(Base):
    """Append-only: edits create a new revision; the AI baseline is never touched."""

    __tablename__ = "overrides"
    __table_args__ = (Index("ix_ovr_lookup", "run_id", "level", "oem_code", "region_code", "product_code", "month"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("forecast_runs.id", ondelete="CASCADE"))
    level: Mapped[str] = mapped_column(String(16))
    oem_code: Mapped[str] = mapped_column(String(32))
    region_code: Mapped[str] = mapped_column(String(8))
    product_code: Mapped[str] = mapped_column(String(32))
    month: Mapped[date] = mapped_column(Date)
    ai_p50_forecast: Mapped[float] = mapped_column(Float)  # revenue USD (as shown to the rep)
    ai_p50_units: Mapped[float] = mapped_column(Float)
    override_basis: Mapped[str] = mapped_column(String(8))  # UNITS | REVENUE
    sales_override_value: Mapped[float] = mapped_column(Float)  # in `override_basis`
    override_units: Mapped[float] = mapped_column(Float)
    override_revenue: Mapped[float] = mapped_column(Float)
    consensus_value: Mapped[float] = mapped_column(Float)  # final plan revenue for this node after the override
    reason_code: Mapped[str] = mapped_column(String(32))
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id: Mapped[str] = mapped_column(String(64))
    timestamp: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    revision: Mapped[int] = mapped_column(Integer, default=1)
    supersedes_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="ACTIVE")  # ACTIVE|SUPERSEDED|WITHDRAWN
    approval_status: Mapped[str] = mapped_column(String(12), default="PENDING")  # PENDING|APPROVED|REJECTED
    approver_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)


class ConsensusPoint(Base):
    """Frozen bottom-level consensus written when a cycle is locked (basis for FVA)."""

    __tablename__ = "consensus_points"
    __table_args__ = (Index("ix_cons_lookup", "run_id", "oem_code", "region_code", "product_code", "month"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("forecast_runs.id", ondelete="CASCADE"))
    oem_code: Mapped[str] = mapped_column(String(32))
    region_code: Mapped[str] = mapped_column(String(8))
    product_code: Mapped[str] = mapped_column(String(32))
    month: Mapped[date] = mapped_column(Date)
    horizon: Mapped[int] = mapped_column(Integer)
    ai_units: Mapped[float] = mapped_column(Float)
    consensus_units: Mapped[float] = mapped_column(Float)
    asp_usd: Mapped[float] = mapped_column(Float)
    naive_units: Mapped[float] = mapped_column(Float)  # seasonal-naive reference frozen at lock time
    overridden: Mapped[bool] = mapped_column(Boolean, default=False)
    reason_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class FvaResult(Base):
    __tablename__ = "fva_results"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)  # origin run ('ALL' = pooled)
    scope: Mapped[str] = mapped_column(String(64))  # ALL | oem:X | region:Y | user:Z | reason:R | product:P
    horizon: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None = all horizons
    n_obs: Mapped[int] = mapped_column(Integer)
    wmape_naive: Mapped[float | None] = mapped_column(Float, nullable=True)
    wmape_ai: Mapped[float | None] = mapped_column(Float, nullable=True)
    wmape_consensus: Mapped[float | None] = mapped_column(Float, nullable=True)
    fva_sales: Mapped[float | None] = mapped_column(Float, nullable=True)  # wMAPE_AI - wMAPE_consensus
    fva_ai: Mapped[float | None] = mapped_column(Float, nullable=True)  # wMAPE_naive - wMAPE_AI
    fva_sales_ci_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    fva_sales_ci_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    significant: Mapped[bool] = mapped_column(Boolean, default=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AuditLog(Base):
    """Append-only, hash-chained audit trail (tamper-evident)."""

    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    user_id: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(48), index=True)
    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    entity_id: Mapped[str] = mapped_column(String(64))
    before: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    after: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class RiskThreshold(Base):
    """Per product / region configurable thresholds (NULL scope = global default)."""

    __tablename__ = "risk_thresholds"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    region_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    min_coverage: Mapped[float] = mapped_column(Float, default=0.60)
    use_historical_baseline: Mapped[bool] = mapped_column(Boolean, default=True)
    concentration_threshold: Mapped[float] = mapped_column(Float, default=0.70)
    concentration_max_stage: Mapped[int] = mapped_column(Integer, default=3)
    min_uplift_share: Mapped[float] = mapped_column(Float, default=0.05)  # uplift must be >=5% of forecast


class RiskAlert(Base):
    __tablename__ = "risk_alerts"
    __table_args__ = (Index("ix_alert_run", "run_id", "alert_type"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("forecast_runs.id", ondelete="CASCADE"))
    alert_type: Mapped[str] = mapped_column(String(28))  # REVENUE_GAP|SUPPLY_BOTTLENECK|PIPELINE_VULNERABILITY
    severity: Mapped[str] = mapped_column(String(8))  # HIGH|MEDIUM|LOW
    oem_code: Mapped[str] = mapped_column(String(32))
    region_code: Mapped[str] = mapped_column(String(8))
    product_code: Mapped[str] = mapped_column(String(32))
    first_month: Mapped[date] = mapped_column(Date)
    last_month: Mapped[date] = mapped_column(Date)
    financial_impact_usd: Mapped[float] = mapped_column(Float)
    title: Mapped[str] = mapped_column(String(160))
    detail: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="OPEN")  # OPEN|ACKNOWLEDGED|RESOLVED
    owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class PlanningCycle(Base):
    __tablename__ = "planning_cycles"
    id: Mapped[int] = mapped_column(primary_key=True)
    cycle_month: Mapped[date] = mapped_column(Date, unique=True)
    status: Mapped[str] = mapped_column(String(20), default="OPEN")  # OPEN|FORECASTED|CONSENSUS|LOCKED
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    lock_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
