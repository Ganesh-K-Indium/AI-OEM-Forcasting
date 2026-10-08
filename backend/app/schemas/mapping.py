from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class AccountOut(BaseModel):
    id: int
    erp_customer_id: str
    name: str
    account_type: str
    country: str | None
    region_code: str | None
    duns: str | None
    tax_id: str | None
    mapped_to: list[dict]
    status: str  # MAPPED | PENDING_REVIEW | UNMAPPED | NEEDS_ALLOCATION


class MappingCandidate(BaseModel):
    mapping_id: int
    account_id: int
    account_name: str
    account_type: str
    suggested_oem: str
    region_code: str
    confidence: float
    source: str
    evidence: dict | None
    created_at: datetime | None


class Allocation(BaseModel):
    oem_code: str
    region_code: str
    allocation_pct: float = Field(gt=0, le=1)


class SetMappingIn(BaseModel):
    allocations: list[Allocation]
    valid_from: date | None = None
    reason: str | None = None


class ReviewMappingIn(BaseModel):
    approve: bool
    oem_code: str | None = None
    region_code: str | None = None
    valid_from: date | None = None


class RuleIn(BaseModel):
    name: str
    rule_type: str = Field(pattern="^(GLOBAL_DUNS|DUNS_EXACT|TAX_ID|ERP_PARENT|DOMAIN|ALIAS_EXACT|NAME_REGEX)$")
    priority: int = 100
    confidence: float = Field(1.0, ge=0, le=1)
    pattern: str | None = None
    target_oem_code: str | None = None
    target_region: str | None = None
    enabled: bool = True


class RuleOut(RuleIn):
    id: int


class OemOut(BaseModel):
    id: int
    code: str
    name: str
    identifiers: list[dict]
    aliases: list[str]


class IdentifierIn(BaseModel):
    id_type: str = Field(pattern="^(DUNS|GLOBAL_DUNS|TAX_ID|ERP_PARENT_ID|DOMAIN)$")
    id_value: str


class AliasIn(BaseModel):
    alias: str
