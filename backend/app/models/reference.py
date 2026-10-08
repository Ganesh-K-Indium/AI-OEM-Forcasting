"""Master / reference data. All OEM mappings live here (never in code)."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, EmbeddingType, JsonType


class Region(Base):
    __tablename__ = "regions"
    code: Mapped[str] = mapped_column(String(8), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))


class Oem(Base):
    __tablename__ = "oems"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    identifiers: Mapped[list[OemIdentifier]] = relationship(back_populates="oem", cascade="all, delete-orphan")
    aliases: Mapped[list[OemAlias]] = relationship(back_populates="oem", cascade="all, delete-orphan")


class OemIdentifier(Base):
    """DUNS / global-ultimate DUNS / Tax-ID / ERP parent id / e-mail domain of a canonical OEM."""

    __tablename__ = "oem_identifiers"
    __table_args__ = (UniqueConstraint("id_type", "id_value"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    oem_id: Mapped[int] = mapped_column(ForeignKey("oems.id", ondelete="CASCADE"))
    id_type: Mapped[str] = mapped_column(String(24))  # DUNS|GLOBAL_DUNS|TAX_ID|ERP_PARENT_ID|DOMAIN
    id_value: Mapped[str] = mapped_column(String(64))
    oem: Mapped[Oem] = relationship(back_populates="identifiers")


class OemAlias(Base):
    __tablename__ = "oem_aliases"
    id: Mapped[int] = mapped_column(primary_key=True)
    oem_id: Mapped[int] = mapped_column(ForeignKey("oems.id", ondelete="CASCADE"))
    alias: Mapped[str] = mapped_column(String(160))
    normalized_alias: Mapped[str] = mapped_column(String(160), index=True)
    embedding: Mapped[list[float] | None] = mapped_column(EmbeddingType(), nullable=True)
    oem: Mapped[Oem] = relationship(back_populates="aliases")


class ProductLine(Base):
    __tablename__ = "product_lines"
    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    family: Mapped[str] = mapped_column(String(64))
    uom: Mapped[str] = mapped_column(String(16), default="kunits")
    lead_time_months: Mapped[int] = mapped_column(Integer, default=2)


class Account(Base):
    """ERP customer master entity (Sold-To, distributor or end customer)."""

    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    erp_customer_id: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    normalized_name: Mapped[str] = mapped_column(String(200), index=True)
    account_type: Mapped[str] = mapped_column(String(16), default="DIRECT")  # DIRECT|DISTRIBUTOR|END_CUSTOMER
    duns: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    global_ultimate_duns: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    tax_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    erp_parent_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    domain: Mapped[str | None] = mapped_column(String(96), nullable=True)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    region_code: Mapped[str | None] = mapped_column(ForeignKey("regions.code"), nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(EmbeddingType(), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class MappingRule(Base):
    """Deterministic rule engine configuration - ordered, editable by admins."""

    __tablename__ = "mapping_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(96))
    # DUNS_EXACT|GLOBAL_DUNS|TAX_ID|ERP_PARENT|DOMAIN|ALIAS_EXACT|NAME_REGEX
    rule_type: Mapped[str] = mapped_column(String(24))
    priority: Mapped[int] = mapped_column(Integer, default=100)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    pattern: Mapped[str | None] = mapped_column(String(256), nullable=True)  # NAME_REGEX only
    target_oem_id: Mapped[int | None] = mapped_column(ForeignKey("oems.id"), nullable=True)  # NAME_REGEX only
    target_region: Mapped[str | None] = mapped_column(ForeignKey("regions.code"), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class AccountOemMapping(Base):
    """Effective-dated Sold-To/End-customer -> OEM x Home Region, with allocation for distributors."""

    __tablename__ = "account_oem_mappings"
    __table_args__ = (Index("ix_aom_account_valid", "account_id", "valid_from", "valid_to"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    oem_id: Mapped[int] = mapped_column(ForeignKey("oems.id"))
    region_code: Mapped[str] = mapped_column(ForeignKey("regions.code"))
    allocation_pct: Mapped[float] = mapped_column(Float, default=1.0)
    source: Mapped[str] = mapped_column(String(24))  # RULE_*|FUZZY|MANUAL
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")  # ACTIVE|PENDING_REVIEW|REJECTED|SUPERSEDED
    valid_from: Mapped[date] = mapped_column(Date, default=date(1900, 1, 1))
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    evidence: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    account: Mapped[Account] = relationship()
    oem: Mapped[Oem] = relationship()


class FxRate(Base):
    __tablename__ = "fx_rates"
    __table_args__ = (UniqueConstraint("month", "currency"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    month: Mapped[date] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3))
    rate_to_usd: Mapped[float] = mapped_column(Float)


class SystemSetting(Base):
    """Admin-editable configuration (thresholds, fuzzy cut-offs, FX policy ...)."""

    __tablename__ = "system_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict | list | str | float | int | bool | None] = mapped_column(JsonType)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
