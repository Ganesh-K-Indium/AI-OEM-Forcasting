"""ERP / CRM fact tables (source-system grain, pre-mapping)."""
from __future__ import annotations

from datetime import date

from sqlalchemy import Boolean, Date, Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class SalesRep(Base):
    __tablename__ = "sales_reps"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(96))
    region_code: Mapped[str] = mapped_column(ForeignKey("regions.code"))


class SalesActual(Base):
    __tablename__ = "sales_actuals"
    __table_args__ = (Index("ix_sales_month", "month"), Index("ix_sales_acct", "account_id", "product_code"))
    id: Mapped[int] = mapped_column(primary_key=True)
    month: Mapped[date] = mapped_column(Date)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    end_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    product_code: Mapped[str] = mapped_column(ForeignKey("product_lines.code"))
    units: Mapped[float] = mapped_column(Float)  # kunits
    revenue_local: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3), default="USD")


class BacklogSnapshot(Base):
    """Confirmed ERP backlog (open POs) as of a snapshot month, scheduled by delivery month."""

    __tablename__ = "backlog_snapshots"
    __table_args__ = (Index("ix_backlog_snap", "snapshot_month", "delivery_month"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_month: Mapped[date] = mapped_column(Date)
    delivery_month: Mapped[date] = mapped_column(Date)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    end_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    product_code: Mapped[str] = mapped_column(ForeignKey("product_lines.code"))
    units: Mapped[float] = mapped_column(Float)
    value_local: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3), default="USD")


class CapacityAllocation(Base):
    __tablename__ = "capacity_allocations"
    __table_args__ = (UniqueConstraint("month", "region_code", "product_code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    month: Mapped[date] = mapped_column(Date)
    region_code: Mapped[str] = mapped_column(ForeignKey("regions.code"))
    product_code: Mapped[str] = mapped_column(ForeignKey("product_lines.code"))
    capacity_units: Mapped[float] = mapped_column(Float)


class Opportunity(Base):
    __tablename__ = "opportunities"
    id: Mapped[int] = mapped_column(primary_key=True)
    sfdc_id: Mapped[str] = mapped_column(String(24), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    end_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    product_code: Mapped[str] = mapped_column(ForeignKey("product_lines.code"))
    rep_id: Mapped[int] = mapped_column(ForeignKey("sales_reps.id"))
    created_month: Mapped[date] = mapped_column(Date)
    is_closed: Mapped[bool] = mapped_column(Boolean, default=False)
    is_won: Mapped[bool] = mapped_column(Boolean, default=False)
    closed_month: Mapped[date | None] = mapped_column(Date, nullable=True)
    first_delivery_month: Mapped[date | None] = mapped_column(Date, nullable=True)  # known only after win
    amount_local: Mapped[float] = mapped_column(Float)  # total deal value
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    quantity_units: Mapped[float] = mapped_column(Float)  # kunits over the ramp
    ramp_months: Mapped[int] = mapped_column(Integer, default=6)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=True)


class OpportunitySnapshot(Base):
    """Monthly point-in-time view of every opp - prevents label/state leakage in training."""

    __tablename__ = "opportunity_snapshots"
    __table_args__ = (UniqueConstraint("opportunity_id", "snapshot_month"), Index("ix_oppsnap_month", "snapshot_month"))
    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"))
    snapshot_month: Mapped[date] = mapped_column(Date)
    stage: Mapped[int] = mapped_column(Integer)  # 1 Prospect,2 Qualify,3 Quote,4 Negotiate,5 Commit,6 Won,7 Lost
    amount_local: Mapped[float] = mapped_column(Float)
    quantity_units: Mapped[float] = mapped_column(Float)
    expected_close_month: Mapped[date] = mapped_column(Date)
    months_in_stage: Mapped[int] = mapped_column(Integer)
    push_count: Mapped[int] = mapped_column(Integer, default=0)
    rep_probability: Mapped[float] = mapped_column(Float)
    quote_issued: Mapped[bool] = mapped_column(Boolean, default=False)


class Contract(Base):
    __tablename__ = "contracts"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    product_code: Mapped[str] = mapped_column(ForeignKey("product_lines.code"))
    start_month: Mapped[date] = mapped_column(Date)
    end_month: Mapped[date] = mapped_column(Date)
    committed_units_per_month: Mapped[float] = mapped_column(Float)
    contract_price_local: Mapped[float] = mapped_column(Float)  # per kunit at contract start
    annual_price_change_pct: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String(3), default="USD")


class MappedSeries(Base):
    """Materialised OEM x Home Region x Product x Month actuals (output of the mapping layer)."""

    __tablename__ = "mapped_series"
    __table_args__ = (UniqueConstraint("month", "oem_code", "region_code", "product_code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    month: Mapped[date] = mapped_column(Date, index=True)
    oem_code: Mapped[str] = mapped_column(String(32))
    region_code: Mapped[str] = mapped_column(String(8))
    product_code: Mapped[str] = mapped_column(String(32))
    units: Mapped[float] = mapped_column(Float)
    revenue_usd: Mapped[float] = mapped_column(Float)  # under the configured FX policy
