from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_workspace
from app.api.aio import in_session
from app.core.audit import audit
from app.core.db import get_adb
from app.core.security import current_user, require_roles
from app.mapping import service
from app.mapping.embeddings import get_embedder
from app.mapping.normalize import normalize_name
from app.models.ops import User
from app.models.reference import Account, AccountOemMapping, MappingRule, Oem, OemAlias, OemIdentifier
from app.schemas.admin import JobOut
from app.schemas.mapping import AccountOut, AliasIn, IdentifierIn, MappingCandidate, OemOut, ReviewMappingIn, RuleIn, RuleOut, SetMappingIn
from app.tasks.jobs import submit_job

router = APIRouter(prefix="/mapping", tags=["mapping"], dependencies=[Depends(current_user), Depends(require_workspace)])
EDIT = ("steward",)


def _accounts(s, status, q, account_type, limit):
    oems = {o.id: o.code for o in s.execute(select(Oem)).scalars()}
    maps: dict[int, list[AccountOemMapping]] = {}
    for m in s.execute(select(AccountOemMapping).where(AccountOemMapping.status.in_(["ACTIVE", "PENDING_REVIEW"]))).scalars():
        maps.setdefault(m.account_id, []).append(m)
    out = []
    for a in s.execute(select(Account).order_by(Account.name)).scalars():
        if (q and q.lower() not in a.name.lower()) or (account_type and a.account_type != account_type):
            continue
        ms = maps.get(a.id, [])
        act = [m for m in ms if m.status == "ACTIVE"]
        st = "MAPPED" if act else "PENDING_REVIEW" if ms else ("NEEDS_ALLOCATION" if a.account_type == "DISTRIBUTOR" else "UNMAPPED")
        if status and st != status:
            continue
        out.append(AccountOut(id=a.id, erp_customer_id=a.erp_customer_id, name=a.name, account_type=a.account_type, country=a.country, region_code=a.region_code, duns=a.duns, tax_id=a.tax_id,
                              mapped_to=[dict(mapping_id=m.id, oem=oems[m.oem_id], region=m.region_code, pct=m.allocation_pct, source=m.source, confidence=m.confidence, status=m.status,
                                              valid_from=str(m.valid_from), valid_to=str(m.valid_to) if m.valid_to else None) for m in ms], status=st))
        if len(out) >= limit:
            break
    return out


@router.get("/accounts", response_model=list[AccountOut])
async def accounts(status: str | None = None, q: str | None = None, account_type: str | None = None, limit: int = 300, db: AsyncSession = Depends(get_adb)):
    return await in_session(db, _accounts, status, q, account_type, limit)


def _queue(s):
    rows = s.execute(select(AccountOemMapping, Account, Oem).join(Account, Account.id == AccountOemMapping.account_id).join(Oem, Oem.id == AccountOemMapping.oem_id)
                     .where(AccountOemMapping.status == "PENDING_REVIEW").order_by(AccountOemMapping.confidence.desc())).all()
    return [MappingCandidate(mapping_id=m.id, account_id=a.id, account_name=a.name, account_type=a.account_type, suggested_oem=o.code, region_code=m.region_code,
                             confidence=m.confidence, source=m.source, evidence=m.evidence, created_at=m.created_at) for m, a, o in rows]


@router.get("/queue", response_model=list[MappingCandidate])
async def review_queue(db: AsyncSession = Depends(get_adb)):
    return await in_session(db, _queue)


def _wrap(fn):
    def w(*a, **kw):
        try:
            return fn(*a, **kw)
        except service.MappingError as e:
            raise HTTPException(422, str(e)) from e
    return w


@_wrap
def _review(s, email, mapping_id, body: ReviewMappingIn):
    m = service.review_mapping(s, mapping_id, body.approve, email, body.oem_code, body.region_code, body.valid_from)
    return {"mapping_id": m.id, "status": m.status}


@router.post("/{mapping_id}/review")
async def review(mapping_id: int, body: ReviewMappingIn, db: AsyncSession = Depends(get_adb), user: User = Depends(require_roles(*EDIT))):
    return await in_session(db, _review, user.email, mapping_id, body, commit=True)


@_wrap
def _set(s, email, account_id, body: SetMappingIn):
    return {"created": [m.id for m in service.set_mapping(s, account_id, [a.model_dump() for a in body.allocations], body.valid_from, email, body.reason)]}


@router.put("/accounts/{account_id}")
async def set_mapping(account_id: int, body: SetMappingIn, db: AsyncSession = Depends(get_adb), user: User = Depends(require_roles(*EDIT))):
    return await in_session(db, _set, user.email, account_id, body, commit=True)


@router.post("/run", response_model=JobOut, status_code=202)
async def run_pipeline(db: AsyncSession = Depends(get_adb), user: User = Depends(require_roles(*EDIT))):
    return JobOut.model_validate(await in_session(db, submit_job, "mapping_pipeline", {}, user.email, db.info["workspace"].id), from_attributes=True)


@router.post("/restate", response_model=JobOut, status_code=202)
async def restate(db: AsyncSession = Depends(get_adb), user: User = Depends(require_roles(*EDIT))):
    """Rebuild OEM x Region x Product history after mapping edits (effective-dated resolution is re-applied)."""
    return JobOut.model_validate(await in_session(db, submit_job, "materialize", {}, user.email, db.info["workspace"].id), from_attributes=True)


def _rule_out(r: MappingRule, oems: dict[int, str]) -> RuleOut:
    return RuleOut(id=r.id, name=r.name, rule_type=r.rule_type, priority=r.priority, confidence=r.confidence, pattern=r.pattern,
                   target_oem_code=oems.get(r.target_oem_id) if r.target_oem_id else None, target_region=r.target_region, enabled=r.enabled)


def _rules(s):
    oems = {o.id: o.code for o in s.execute(select(Oem)).scalars()}
    return [_rule_out(r, oems) for r in s.execute(select(MappingRule).order_by(MappingRule.priority)).scalars()]


@router.get("/rules", response_model=list[RuleOut])
async def rules(db: AsyncSession = Depends(get_adb)):
    return await in_session(db, _rules)


def _apply_rule(s, r: MappingRule, body: RuleIn) -> None:
    r.name, r.rule_type, r.priority, r.confidence, r.pattern, r.target_region, r.enabled = body.name, body.rule_type, body.priority, body.confidence, body.pattern, body.target_region, body.enabled
    if body.target_oem_code:
        oem = s.execute(select(Oem).where(Oem.code == body.target_oem_code)).scalar_one_or_none()
        if oem is None:
            raise HTTPException(422, f"unknown OEM {body.target_oem_code}")
        r.target_oem_id = oem.id
    if body.rule_type == "NAME_REGEX":
        try:
            re.compile(body.pattern or "")
        except re.error as e:
            raise HTTPException(422, f"invalid regex: {e}") from e
        if not r.target_oem_id:
            raise HTTPException(422, "NAME_REGEX rules need target_oem_code")


def _create_rule(s, email, body: RuleIn):
    r = MappingRule()
    _apply_rule(s, r, body)
    s.add(r)
    s.flush()
    audit(s, email, "MAPPING_RULE_CREATE", "mapping_rule", r.id, None, body.model_dump())
    return _rule_out(r, {o.id: o.code for o in s.execute(select(Oem)).scalars()})


@router.post("/rules", response_model=RuleOut, status_code=201)
async def create_rule(body: RuleIn, db: AsyncSession = Depends(get_adb), user: User = Depends(require_roles(*EDIT))):
    return await in_session(db, _create_rule, user.email, body, commit=True)


def _update_rule(s, email, rule_id, body: RuleIn):
    r = s.get(MappingRule, rule_id)
    if r is None:
        raise HTTPException(404, "rule not found")
    before = dict(name=r.name, priority=r.priority, enabled=r.enabled, confidence=r.confidence)
    _apply_rule(s, r, body)
    audit(s, email, "MAPPING_RULE_UPDATE", "mapping_rule", r.id, before, body.model_dump())
    return _rule_out(r, {o.id: o.code for o in s.execute(select(Oem)).scalars()})


@router.put("/rules/{rule_id}", response_model=RuleOut)
async def update_rule(rule_id: int, body: RuleIn, db: AsyncSession = Depends(get_adb), user: User = Depends(require_roles(*EDIT))):
    return await in_session(db, _update_rule, user.email, rule_id, body, commit=True)


def _delete_rule(s, email, rule_id):
    r = s.get(MappingRule, rule_id)
    if r is None:
        raise HTTPException(404, "rule not found")
    audit(s, email, "MAPPING_RULE_DELETE", "mapping_rule", r.id, dict(name=r.name), None)
    s.delete(r)
    return {"deleted": rule_id}


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: int, db: AsyncSession = Depends(get_adb), user: User = Depends(require_roles(*EDIT))):
    return await in_session(db, _delete_rule, user.email, rule_id, commit=True)


def _oems(s):
    return [OemOut(id=o.id, code=o.code, name=o.name, identifiers=[dict(id=i.id, id_type=i.id_type, id_value=i.id_value) for i in o.identifiers], aliases=[a.alias for a in o.aliases])
            for o in s.execute(select(Oem).order_by(Oem.code)).scalars()]


@router.get("/oems", response_model=list[OemOut])
async def oems(db: AsyncSession = Depends(get_adb)):
    return await in_session(db, _oems)


def _add_identifier(s, email, oem_id, body: IdentifierIn):
    if s.get(Oem, oem_id) is None:
        raise HTTPException(404, "OEM not found")
    if s.execute(select(OemIdentifier).where(OemIdentifier.id_type == body.id_type, OemIdentifier.id_value == body.id_value)).first():
        raise HTTPException(409, "identifier already assigned to an OEM")
    i = OemIdentifier(oem_id=oem_id, **body.model_dump())
    s.add(i)
    s.flush()
    audit(s, email, "OEM_IDENTIFIER_ADD", "oem", oem_id, None, body.model_dump())
    return {"id": i.id}


@router.post("/oems/{oem_id}/identifiers", status_code=201)
async def add_identifier(oem_id: int, body: IdentifierIn, db: AsyncSession = Depends(get_adb), user: User = Depends(require_roles(*EDIT))):
    return await in_session(db, _add_identifier, user.email, oem_id, body, commit=True)


def _del_identifier(s, email, identifier_id):
    i = s.get(OemIdentifier, identifier_id)
    if i is None:
        raise HTTPException(404, "not found")
    audit(s, email, "OEM_IDENTIFIER_DELETE", "oem", i.oem_id, dict(id_type=i.id_type, id_value=i.id_value), None)
    s.delete(i)
    return {"deleted": identifier_id}


@router.delete("/identifiers/{identifier_id}")
async def delete_identifier(identifier_id: int, db: AsyncSession = Depends(get_adb), user: User = Depends(require_roles(*EDIT))):
    return await in_session(db, _del_identifier, user.email, identifier_id, commit=True)


def _add_alias(s, email, oem_id, body: AliasIn):
    if s.get(Oem, oem_id) is None:
        raise HTTPException(404, "OEM not found")
    a = OemAlias(oem_id=oem_id, alias=body.alias, normalized_alias=normalize_name(body.alias), embedding=get_embedder().embed([body.alias])[0])
    s.add(a)
    s.flush()
    audit(s, email, "OEM_ALIAS_ADD", "oem", oem_id, None, body.model_dump())
    return {"id": a.id}


@router.post("/oems/{oem_id}/aliases", status_code=201)
async def add_alias(oem_id: int, body: AliasIn, db: AsyncSession = Depends(get_adb), user: User = Depends(require_roles(*EDIT))):
    return await in_session(db, _add_alias, user.email, oem_id, body, commit=True)
