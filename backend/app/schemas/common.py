from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


Role = Literal["admin", "planner", "sales_rep", "steward", "viewer"]
ReasonCode = Literal["PROJECT_DELAY", "NEW_WIN", "CAPACITY_CAP", "CUSTOMER_DIRECT_GUIDANCE"]
Level = Literal["TOTAL", "REGION", "OEM", "PRODUCT", "OEM_REGION", "BOTTOM"]


class UserOut(ORM):
    email: str
    full_name: str
    role: Role
    scope_oems: list[str] | None = None
    scope_regions: list[str] | None = None


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class LoginIn(BaseModel):
    email: str
    password: str


class Meta(BaseModel):
    synthetic_mode: bool
    label: str
    current_run_id: str | None
    cycle_month: date | None
    cycle_status: str | None
    horizon: int
    fx_policy: str
    currency: str = "USD"
    units_label: str = "kunits"
    version: str
    workspace: dict | None = None
    capabilities: dict | None = None


class RunOut(ORM):
    id: str
    cycle_month: date
    kind: str
    status: str
    horizon: int
    is_synthetic: bool
    created_at: datetime
    locked_at: datetime | None = None
    summary: dict | None = None
    error: str | None = None


class FilterOptions(BaseModel):
    oems: list[str]
    regions: list[str]
    products: list[dict]
    horizons: list[int]
