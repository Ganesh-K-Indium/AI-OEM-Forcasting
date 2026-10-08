from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, JsonType


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_type: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(12), default="PENDING")  # PENDING|RUNNING|SUCCESS|FAILED
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    params: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    result: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class DqResult(Base):
    __tablename__ = "dq_results"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_ts: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    check_name: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(8))  # ERROR|WARN|INFO
    passed: Mapped[bool] = mapped_column(Boolean)
    metric: Mapped[float | None] = mapped_column(Float, nullable=True)
    threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    message: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    batch_id: Mapped[str] = mapped_column(String(36), index=True)


class DriftReport(Base):
    __tablename__ = "drift_reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    kind: Mapped[str] = mapped_column(String(16))  # FEATURE|ERROR
    name: Mapped[str] = mapped_column(String(64))
    value: Mapped[float] = mapped_column(Float)
    threshold: Mapped[float] = mapped_column(Float)
    breached: Mapped[bool] = mapped_column(Boolean)
    detail: Mapped[dict | None] = mapped_column(JsonType, nullable=True)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(128), unique=True)
    full_name: Mapped[str] = mapped_column(String(96))
    password_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    role: Mapped[str] = mapped_column(String(16))  # admin|planner|sales_rep|steward|viewer
    scope_oems: Mapped[list | None] = mapped_column(JsonType, nullable=True)  # row-level scope for sales_rep
    scope_regions: Mapped[list | None] = mapped_column(JsonType, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
