from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.schemas.common import Role


class JobOut(BaseModel):
    id: str
    job_type: str
    state: str
    progress: float
    message: str | None
    params: dict | None
    result: dict | None
    error: str | None
    created_by: str | None
    created_at: datetime
    finished_at: datetime | None


class JobIn(BaseModel):
    job_type: str
    params: dict | None = None


class DqOut(BaseModel):
    check_name: str
    severity: str
    passed: bool
    metric: float | None
    threshold: float | None
    message: str
    run_ts: datetime


class SettingOut(BaseModel):
    key: str
    value: object
    description: str | None


class SettingIn(BaseModel):
    value: object


class UserIn(BaseModel):
    email: str
    full_name: str
    password: str
    role: Role
    scope_oems: list[str] | None = None
    scope_regions: list[str] | None = None


class DriftOut(BaseModel):
    run_id: str
    kind: str
    name: str
    value: float
    threshold: float
    breached: bool
