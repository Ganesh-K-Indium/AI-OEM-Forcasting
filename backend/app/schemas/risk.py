from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class AlertOut(BaseModel):
    id: int
    run_id: str
    alert_type: str
    severity: str
    oem: str
    region: str
    product: str
    first_month: date
    last_month: date
    financial_impact_usd: float
    title: str
    detail: dict | None
    status: str
    owner: str | None
    note: str | None


class AlertPatch(BaseModel):
    status: str | None = Field(None, pattern="^(OPEN|ACKNOWLEDGED|RESOLVED)$")
    owner: str | None = None
    note: str | None = None


class ThresholdIn(BaseModel):
    product_code: str | None = None
    region_code: str | None = None
    min_coverage: float = Field(0.60, ge=0, le=1.5)
    use_historical_baseline: bool = True
    concentration_threshold: float = Field(0.70, ge=0, le=1)
    concentration_max_stage: int = Field(3, ge=1, le=5)
    min_uplift_share: float = Field(0.05, ge=0, le=1)


class ThresholdOut(ThresholdIn):
    id: int


class RiskSummary(BaseModel):
    run_id: str
    open_alerts: int
    by_type: dict[str, dict]
    revenue_at_risk_usd: float
    top_oems: list[dict]
