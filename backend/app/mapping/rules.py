"""Deterministic rule engine. Rules (type / priority / confidence) and the OEM identifiers they match
against are DB rows editable by admins - there is no mapping logic keyed on hardcoded names."""
from __future__ import annotations

from datetime import date

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.mapping.normalize import normalize_name
from app.models.reference import Account, AccountOemMapping, MappingRule, OemAlias, OemIdentifier

IDENT_RULES = {  # rule_type -> (account attribute, OEM identifier type)
    "GLOBAL_DUNS": ("global_ultimate_duns", "GLOBAL_DUNS"),
    "DUNS_EXACT": ("duns", "DUNS"),
    "TAX_ID": ("tax_id", "TAX_ID"),
    "ERP_PARENT": ("erp_parent_id", "ERP_PARENT_ID"),
    "DOMAIN": ("domain", "DOMAIN"),
}
DEFAULT_RULES = [
    ("Global-ultimate DUNS lookup", "GLOBAL_DUNS", 10, 1.00), ("Site DUNS lookup", "DUNS_EXACT", 20, 1.00),
    ("Tax-ID lookup", "TAX_ID", 30, 0.99), ("ERP customer-master parent", "ERP_PARENT", 40, 0.98),
    ("E-mail domain", "DOMAIN", 50, 0.95), ("Exact OEM alias", "ALIAS_EXACT", 60, 0.97),
]


@dataclass
class RuleHit:
    rule_id: int
    rule_type: str
    priority: int
    confidence: float
    oem_id: int
    region_code: str | None


def seed_default_rules(session: Session) -> None:
    if session.execute(select(MappingRule.id).limit(1)).first():
        return
    for name, typ, prio, conf in DEFAULT_RULES:
        session.add(MappingRule(name=name, rule_type=typ, priority=prio, confidence=conf, enabled=True))
    session.flush()


def evaluate_account(acc: Account, rules: list[MappingRule], ident: dict[tuple[str, str], set[int]],
                     alias_idx: dict[str, set[int]]) -> list[RuleHit]:
    hits: list[RuleHit] = []
    for r in rules:
        oems: set[int] = set()
        region = acc.region_code
        if r.rule_type in IDENT_RULES:
            attr, itype = IDENT_RULES[r.rule_type]
            val = getattr(acc, attr)
            if val:
                oems = ident.get((itype, str(val)), set())
        elif r.rule_type == "ALIAS_EXACT":
            oems = alias_idx.get(acc.normalized_name, set())
        elif r.rule_type == "NAME_REGEX" and r.pattern and r.target_oem_id and re.search(r.pattern, acc.name, flags=re.I):
            oems, region = {r.target_oem_id}, r.target_region or region
        for o in oems:
            hits.append(RuleHit(r.id, r.rule_type, r.priority, r.confidence, o, region))
    return hits


def run_rule_engine(session: Session, user: str = "system:rules", valid_from=None) -> dict:
    """Maps every account that has no ACTIVE/PENDING mapping yet. Distributors are never auto-mapped
    (they need an explicit allocation across OEMs)."""
    rules = list(session.execute(select(MappingRule).where(MappingRule.enabled).order_by(MappingRule.priority)).scalars())
    ident: dict[tuple[str, str], set[int]] = {}
    for i in session.execute(select(OemIdentifier)).scalars():
        ident.setdefault((i.id_type, i.id_value), set()).add(i.oem_id)
    alias_idx: dict[str, set[int]] = {}
    for a in session.execute(select(OemAlias)).scalars():
        alias_idx.setdefault(normalize_name(a.alias), set()).add(a.oem_id)
    already = {r for (r,) in session.execute(select(AccountOemMapping.account_id).where(AccountOemMapping.status.in_(["ACTIVE", "PENDING_REVIEW"])))}
    out = dict(mapped=0, conflicts=0, skipped_distributors=0, no_match=0)
    for acc in session.execute(select(Account).where(Account.is_active)).scalars():
        if acc.id in already:
            continue
        if acc.account_type == "DISTRIBUTOR":
            out["skipped_distributors"] += 1
            continue
        hits = evaluate_account(acc, rules, ident, alias_idx)
        if not hits:
            out["no_match"] += 1
            continue
        best = min(hits, key=lambda h: h.priority)
        distinct = {h.oem_id for h in hits}
        conflict = len(distinct) > 1
        status = "PENDING_REVIEW" if (conflict or not best.region_code) else "ACTIVE"
        session.add(AccountOemMapping(
            account_id=acc.id, oem_id=best.oem_id, region_code=best.region_code or "AMER", allocation_pct=1.0, source=f"RULE_{best.rule_type}",
            confidence=best.confidence if not conflict else 0.5, status=status, valid_from=valid_from or date(1900, 1, 1),
            evidence={"hits": [h.__dict__ for h in hits], "conflict": conflict}, created_by=user))
        out["conflicts" if conflict else "mapped"] += 1
    session.flush()
    return out
