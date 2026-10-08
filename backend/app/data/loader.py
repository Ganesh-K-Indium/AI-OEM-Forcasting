"""Persist a SyntheticBundle (or any adapter producing the same frames) into the operational DB."""
from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.core.db import Base
from app.core.settings_store import seed_defaults
from app.data.synthetic import REGIONS, SyntheticBundle
from app.mapping import fuzzy, service
from app.mapping.normalize import normalize_name
from app.mapping.rules import seed_default_rules
from app.models.facts import (BacklogSnapshot, CapacityAllocation, Contract, Opportunity, OpportunitySnapshot, SalesActual, SalesRep)
from app.models.ops import User
from app.models.reference import Account, FxRate, Oem, OemAlias, OemIdentifier, ProductLine, Region, SystemSetting

KEEP_TABLES = {"users"}


def _records(df: pd.DataFrame, cols: list[str]) -> list[dict]:
    d = df[cols].astype(object).where(df[cols].notna(), None)
    return d.to_dict("records")


def wipe_domain_data(session: Session) -> None:
    for t in reversed(Base.metadata.sorted_tables):
        if t.name not in KEEP_TABLES:
            session.execute(delete(t))
    session.flush()


def load_bundle(session: Session, b: SyntheticBundle, user: str = "seed") -> dict:
    seed_defaults(session)
    seed_default_rules(session)
    session.execute(insert(Region), [dict(code=c, name=n) for c, n in REGIONS.items()])
    session.execute(insert(Oem), _records(b.oems, ["id", "code", "name"]))
    session.execute(insert(OemIdentifier), _records(b.identifiers, ["oem_id", "id_type", "id_value"]))
    al = b.aliases.assign(normalized_alias=b.aliases.alias.map(normalize_name))
    session.execute(insert(OemAlias), _records(al, ["oem_id", "alias", "normalized_alias"]))
    session.execute(insert(ProductLine), _records(b.products, ["code", "name", "family", "lead_time_months"]))
    acc = b.accounts.assign(normalized_name=b.accounts.name.map(normalize_name), is_active=True)
    session.execute(insert(Account), _records(acc, ["id", "erp_customer_id", "name", "normalized_name", "account_type", "duns", "global_ultimate_duns",
                                                    "tax_id", "erp_parent_id", "domain", "country", "region_code", "is_active"]))
    session.execute(insert(SalesRep), _records(b.reps, ["id", "name", "region_code"]))
    fx = b.fx
    session.execute(insert(FxRate), _records(fx, ["month", "currency", "rate_to_usd"]))
    for chunk in np.array_split(b.sales, max(1, len(b.sales) // 8000)):
        session.execute(insert(SalesActual), _records(chunk.assign(end_account_id=chunk.end_account_id.astype("Int64").astype(object)), ["month", "account_id", "end_account_id", "product_code", "units", "revenue_local", "currency"]))
    bl = b.backlog.assign(end_account_id=b.backlog.end_account_id.astype("Int64").astype(object))
    for chunk in np.array_split(bl, max(1, len(bl) // 8000)):
        session.execute(insert(BacklogSnapshot), _records(chunk, ["snapshot_month", "delivery_month", "account_id", "end_account_id", "product_code", "units", "value_local", "currency"]))
    session.execute(insert(CapacityAllocation), _records(b.capacity, ["month", "region_code", "product_code", "capacity_units"]))
    o = b.opportunities.assign(end_account_id=b.opportunities.end_account_id.astype("Int64").astype(object), is_synthetic=True)
    session.execute(insert(Opportunity), _records(o, ["id", "sfdc_id", "name", "account_id", "end_account_id", "product_code", "rep_id", "created_month", "is_closed", "is_won",
                                                      "closed_month", "first_delivery_month", "amount_local", "currency", "quantity_units", "ramp_months", "is_synthetic"]))
    for chunk in np.array_split(b.snapshots, max(1, len(b.snapshots) // 8000)):
        session.execute(insert(OpportunitySnapshot), _records(chunk, ["opportunity_id", "snapshot_month", "stage", "amount_local", "quantity_units",
                                                                      "expected_close_month", "months_in_stage", "push_count", "rep_probability", "quote_issued"]))
    if len(b.contracts):
        session.execute(insert(Contract), _records(b.contracts, ["account_id", "product_code", "start_month", "end_month", "committed_units_per_month",
                                                                 "contract_price_local", "currency", "annual_price_change_pct"]))
    session.flush()
    # admin-curated distributor allocations -> DB mapping tables (never hardcoded in code)
    for acct_id, g in b.manual_mappings.groupby("account_id"):
        service.set_mapping(session, int(acct_id), [dict(oem_code=r.oem_code, region_code=r.region_code, allocation_pct=float(r.allocation_pct))
                                                    for r in g.itertuples()], None, user, reason="Seeded distributor allocation (synthetic)")
    session.add(SystemSetting(key="synthetic_scenarios", value=b.scenarios, description="Ground-truth scenario tag per series (synthetic mode only)", updated_by=user)) \
        if session.get(SystemSetting, "synthetic_scenarios") is None else None
    session.flush()
    return dict(accounts=len(acc), sales=len(b.sales), backlog=len(b.backlog), opportunities=len(b.opportunities), snapshots=len(b.snapshots))


def steward_replay(session: Session, b: SyntheticBundle, user: str = "steward.demo") -> dict:
    """Demo-only: emulate a data steward who has already worked most of the review queue.
    Approves pending fuzzy candidates that match ground truth; leaves decoys / ambiguous ones pending."""
    from app.models.reference import AccountOemMapping

    truth = b.accounts.set_index("id")["true_oem"].to_dict()
    codes = {o.id: o.code for o in session.execute(select(Oem)).scalars()}
    approved = left = 0
    pend = list(session.execute(select(AccountOemMapping).where(AccountOemMapping.status == "PENDING_REVIEW", AccountOemMapping.source == "FUZZY")).scalars())
    for i, m in enumerate(sorted(pend, key=lambda x: -x.confidence)):
        if truth.get(m.account_id) == codes[m.oem_id] and i % 9 != 8:  # leave a handful pending for the demo
            service.review_mapping(session, m.id, True, user)
            approved += 1
        else:
            left += 1
    return dict(approved=approved, left_pending=left)


def ensure_demo_users(session: Session) -> None:
    from app.core.security import hash_password

    if session.execute(select(User.id).limit(1)).first():
        return
    demo = [("admin@demo.local", "Alex Admin", "admin", None, None), ("planner@demo.local", "Priya Planner", "planner", None, None),
            ("steward@demo.local", "Sam Steward", "steward", None, None), ("viewer@demo.local", "Vic Viewer", "viewer", None, None),
            ("rep.amer@demo.local", "Riley Rep (AMER)", "sales_rep", None, ["AMER"]), ("rep.emea@demo.local", "Emre Rep (EMEA)", "sales_rep", None, ["EMEA"]),
            ("rep.apac@demo.local", "Aiko Rep (APAC)", "sales_rep", None, ["APAC"])]
    for email, name, role, oems, regs in demo:
        session.add(User(email=email, full_name=name, role=role, scope_oems=oems, scope_regions=regs, password_hash=hash_password("demo1234")))
    session.flush()
