from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, JsonType


class ForecastRun(Base):
    __tablename__ = "forecast_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    cycle_month: Mapped[date] = mapped_column(Date, index=True)  # last actual month used
    horizon: Mapped[int] = mapped_column(Integer, default=12)
    kind: Mapped[str] = mapped_column(String(12), default="CURRENT")  # CURRENT | REPLAY
    status: Mapped[str] = mapped_column(String(16), default="RUNNING")  # RUNNING|COMPLETED|FAILED
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    summary: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    data_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    locked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


class SeriesSegment(Base):
    __tablename__ = "series_segments"
    __table_args__ = (Index("ix_segment_run", "run_id", "level"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("forecast_runs.id", ondelete="CASCADE"))
    level: Mapped[str] = mapped_column(String(16))
    oem_code: Mapped[str] = mapped_column(String(32))
    region_code: Mapped[str] = mapped_column(String(8))
    product_code: Mapped[str] = mapped_column(String(32))
    n_obs: Mapped[int] = mapped_column(Integer)
    adi: Mapped[float] = mapped_column(Float)
    cv2: Mapped[float] = mapped_column(Float)
    seasonality_strength: Mapped[float] = mapped_column(Float)
    acf12: Mapped[float | None] = mapped_column(Float, nullable=True)
    exog_strength: Mapped[float] = mapped_column(Float)
    segment: Mapped[str] = mapped_column(String(24))  # smooth|erratic|seasonal|intermittent|lumpy|complex
    champion_model: Mapped[str | None] = mapped_column(String(48), nullable=True)
    scenario_tag: Mapped[str | None] = mapped_column(String(48), nullable=True)


class ForecastPoint(Base):
    """AI forecast (immutable once the run is locked). Overrides live in a separate table."""

    __tablename__ = "forecast_points"
    __table_args__ = (Index("ix_fp_lookup", "run_id", "level", "oem_code", "region_code", "product_code", "month"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("forecast_runs.id", ondelete="CASCADE"))
    level: Mapped[str] = mapped_column(String(16))  # TOTAL|REGION|OEM|OEM_REGION|PRODUCT|BOTTOM
    oem_code: Mapped[str] = mapped_column(String(32))  # 'ALL' for aggregates
    region_code: Mapped[str] = mapped_column(String(8))
    product_code: Mapped[str] = mapped_column(String(32))
    month: Mapped[date] = mapped_column(Date)
    horizon: Mapped[int] = mapped_column(Integer)
    units_p10: Mapped[float] = mapped_column(Float)
    units_p50: Mapped[float] = mapped_column(Float)
    units_p90: Mapped[float] = mapped_column(Float)
    baseline_units_p50: Mapped[float] = mapped_column(Float)  # raw Tier-1 (pre-reconciliation)
    uplift_units: Mapped[float] = mapped_column(Float, default=0.0)  # net commercial uplift (Tier-2)
    gross_uplift_units: Mapped[float] = mapped_column(Float, default=0.0)
    asp_usd: Mapped[float] = mapped_column(Float)  # USD per kunit
    revenue_p10: Mapped[float] = mapped_column(Float)
    revenue_p50: Mapped[float] = mapped_column(Float)
    revenue_p90: Mapped[float] = mapped_column(Float)
    model_name: Mapped[str | None] = mapped_column(String(48), nullable=True)
    segment: Mapped[str | None] = mapped_column(String(24), nullable=True)


class UpliftDetail(Base):
    """Opportunity-level expected units per month: feeds concentration alerts and drill-down."""

    __tablename__ = "uplift_details"
    __table_args__ = (Index("ix_uplift_run", "run_id", "oem_code", "region_code", "product_code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("forecast_runs.id", ondelete="CASCADE"))
    opportunity_id: Mapped[int] = mapped_column(Integer)
    sfdc_id: Mapped[str] = mapped_column(String(24))
    oem_code: Mapped[str] = mapped_column(String(32))
    region_code: Mapped[str] = mapped_column(String(8))
    product_code: Mapped[str] = mapped_column(String(32))
    month: Mapped[date] = mapped_column(Date)
    stage: Mapped[int] = mapped_column(Integer)
    win_prob: Mapped[float] = mapped_column(Float)
    rep_probability: Mapped[float] = mapped_column(Float)
    expected_units: Mapped[float] = mapped_column(Float)  # gross (before net factor)
    unweighted_units: Mapped[float] = mapped_column(Float)


class ModelBenchmark(Base):
    __tablename__ = "model_benchmarks"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("forecast_runs.id", ondelete="CASCADE"), index=True)
    model_name: Mapped[str] = mapped_column(String(48))
    model_family: Mapped[str] = mapped_column(String(24))  # StatsForecast|LightGBM|Chronos-2|TiRex-2|Baseline|Ensemble
    segment: Mapped[str] = mapped_column(String(24))
    horizon: Mapped[int] = mapped_column(Integer)
    wmape: Mapped[float | None] = mapped_column(Float, nullable=True)
    mae: Mapped[float | None] = mapped_column(Float, nullable=True)
    mase: Mapped[float | None] = mapped_column(Float, nullable=True)
    bias: Mapped[float | None] = mapped_column(Float, nullable=True)
    pinball: Mapped[float | None] = mapped_column(Float, nullable=True)
    coverage80: Mapped[float | None] = mapped_column(Float, nullable=True)
    n_obs: Mapped[int] = mapped_column(Integer, default=0)
    is_champion: Mapped[bool] = mapped_column(Boolean, default=False)
    prior_models: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="OK")  # OK | UNAVAILABLE
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class ModelRegistryEntry(Base):
    __tablename__ = "model_registry"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("forecast_runs.id", ondelete="CASCADE"), index=True)
    model_name: Mapped[str] = mapped_column(String(48))
    version: Mapped[str] = mapped_column(String(32))
    params: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    library_versions: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    artifact_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    metrics: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    trained_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
