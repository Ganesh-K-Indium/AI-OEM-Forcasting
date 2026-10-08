from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel

from app.schemas.common import Level, ReasonCode


class HistPoint(BaseModel):
    month: date
    units: float
    revenue: float
    asp: float | None = None


class FcPoint(BaseModel):
    month: date
    horizon: int
    units_p10: float
    units_p50: float
    units_p90: float
    revenue_p10: float
    revenue_p50: float
    revenue_p90: float
    baseline_units: float
    uplift_units: float
    gross_uplift_units: float
    uplift_revenue: float
    asp_usd: float
    override_revenue: float | None = None
    override_units: float | None = None
    override_reason: ReasonCode | None = None
    consensus_units: float
    consensus_revenue: float
    backlog_value: float | None = None
    coverage: float | None = None
    capacity_units: float | None = None
    model_name: str | None = None


class ExplorerOut(BaseModel):
    run_id: str
    level: Level
    oem: str
    region: str
    product: str
    segment: str | None
    champion_model: str | None
    scenario_tag: str | None = None
    cycle_month: date
    synthetic: bool
    history: list[HistPoint]
    forecast: list[FcPoint]
    totals: dict[str, float]


class OppRow(BaseModel):
    opportunity_id: int
    sfdc_id: str
    oem: str
    region: str
    product: str
    stage: int
    win_prob: float
    rep_probability: float
    expected_units: float
    unweighted_units: float
    share_of_uplift: float


class OverrideOut(BaseModel):
    id: int
    run_id: str
    level: Level
    oem: str
    region: str
    product: str
    month: date
    ai_p50_forecast: float
    ai_p50_units: float
    override_basis: str
    sales_override_value: float
    override_units: float
    override_revenue: float
    consensus_value: float
    reason_code: ReasonCode
    comment: str | None
    user_id: str
    timestamp: datetime
    revision: int
    status: str
    approval_status: str
    is_synthetic: bool


class FvaOut(BaseModel):
    run_id: str
    scope: str
    horizon: int | None
    n_obs: int
    wmape_naive: float | None
    wmape_ai: float | None
    wmape_consensus: float | None
    fva_sales: float | None
    fva_ai: float | None
    fva_sales_ci_low: float | None
    fva_sales_ci_high: float | None
    significant: bool


class AuditOut(BaseModel):
    id: int
    ts: datetime
    user_id: str
    action: str
    entity_type: str
    entity_id: str
    before: dict | list | None
    after: dict | list | None
    hash: str


class DetailOut(BaseModel):
    explorer: ExplorerOut
    opportunities: list[OppRow]
    overrides: list[OverrideOut]
    fva: list[FvaOut]
    audit: list[AuditOut]
    coverage: list[dict]
    drivers: dict


class OverrideIn(BaseModel):
    run_id: str
    oem: str = "ALL"
    region: str = "ALL"
    product: str = "ALL"
    month: date
    basis: str = "REVENUE"
    value: float
    reason_code: ReasonCode
    comment: str | None = None


class ReviewIn(BaseModel):
    approve: bool
    note: str | None = None


class BenchmarkRow(BaseModel):
    model_name: str
    model_family: str
    segment: str
    horizon: int
    wmape: float | None
    mae: float | None
    mase: float | None
    bias: float | None
    pinball: float | None
    coverage80: float | None
    n_obs: int
    is_champion: bool
    status: str
    note: str | None = None
    prior_models: str | None = None


class BenchmarkOut(BaseModel):
    run_id: str
    rows: list[BenchmarkRow]
    champions: list[dict]
    segments: dict[str, int]
    backtest_origins: list[str]
    notes: list[str]
