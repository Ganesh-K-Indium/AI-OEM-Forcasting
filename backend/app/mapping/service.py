"""Mapping orchestration, steward actions, effective-dated resolution and materialisation of OEM series."""
from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd
from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.calendar import to_month
from app.core.settings_store import get_setting
from app.mapping import fuzzy, rules
from app.mapping.normalize import normalize_name
from app.models.facts import MappedSeries, SalesActual
from app.models.reference import Account, AccountOemMapping, FxRate, Oem

UNMAPPED = ("UNMAPPED", "UNK")
OPEN_START = date(1900, 1, 1)


class MappingError(ValueError):
    pass


def run_mapping_pipeline(session: Session, user: str = "system") -> dict:
    rules.seed_default_rules(session)
    for a in session.execute(select(Account).where(Account.normalized_name == "")).scalars():
        a.normalized_name = normalize_name(a.name)
    fuzzy.ensure_embeddings(session)
    r = rules.run_rule_engine(session, user="system:rules")
    f = fuzzy.run_fuzzy_mapper(session, user="system:fuzzy")
    audit(session, user, "MAPPING_PIPELINE_RUN", "mapping", "all", None, {"rules": r, "fuzzy": f})
    return {"rules": r, "fuzzy": f}


def _close_active(session: Session, account_id: int, new_from: date, keep_ids: set[int] = frozenset()) -> None:
    for m in session.execute(select(AccountOemMapping).where(AccountOemMapping.account_id == account_id,
                                                              AccountOemMapping.status == "ACTIVE")).scalars():
        if m.id in keep_ids:
            continue
        if m.valid_from >= new_from:
            m.status = "SUPERSEDED"
            m.valid_to = m.valid_from
        elif m.valid_to is None or m.valid_to > new_from:
            m.valid_to = new_from  # exclusive: the old mapping applies strictly before new_from


def set_mapping(session: Session, account_id: int, allocations: list[dict], valid_from: date | None, user: str, reason: str | None = None) -> list[AccountOemMapping]:
    """Admin / steward edit. `allocations` = [{oem_code, region_code, allocation_pct}] summing to 1.0.
    Closes previous ACTIVE rows at `valid_from` (history preserved, effective-dated)."""
    if not allocations:
        raise MappingError("at least one allocation required")
    total = sum(float(a["allocation_pct"]) for a in allocations)
    if abs(total - 1.0) > 1e-6:
        raise MappingError(f"allocation_pct must sum to 1.0 (got {total:.6f})")
    if any(float(a["allocation_pct"]) <= 0 for a in allocations):
        raise MappingError("allocation_pct must be > 0")
    if len({(a["oem_code"], a["region_code"]) for a in allocations}) != len(allocations):
        raise MappingError("duplicate OEM/region in allocations")
    acc = session.get(Account, account_id)
    if acc is None:
        raise MappingError("unknown account")
    if acc.account_type != "DISTRIBUTOR" and len(allocations) > 1:
        raise MappingError("only distributor accounts may split across several OEMs")
    vf = valid_from or OPEN_START
    before = [dict(id=m.id, oem_id=m.oem_id, pct=m.allocation_pct, status=m.status) for m in session.execute(
        select(AccountOemMapping).where(AccountOemMapping.account_id == account_id, AccountOemMapping.status == "ACTIVE")).scalars()]
    _close_active(session, account_id, vf)
    for m in session.execute(select(AccountOemMapping).where(AccountOemMapping.account_id == account_id, AccountOemMapping.status == "PENDING_REVIEW")).scalars():
        m.status = "SUPERSEDED"
    created = []
    for a in allocations:
        oem = session.execute(select(Oem).where(Oem.code == a["oem_code"])).scalar_one_or_none()
        if oem is None:
            raise MappingError(f"unknown OEM {a['oem_code']}")
        m = AccountOemMapping(account_id=account_id, oem_id=oem.id, region_code=a["region_code"], allocation_pct=float(a["allocation_pct"]),
                              source="MANUAL", confidence=1.0, status="ACTIVE", valid_from=vf, created_by=user, reviewed_by=user,
                              reviewed_at=datetime.utcnow(), evidence={"reason": reason})
        session.add(m)
        created.append(m)
    session.flush()
    audit(session, user, "MAPPING_SET", "account", account_id, before, [dict(id=m.id, oem=m.oem_id, pct=m.allocation_pct, valid_from=str(vf)) for m in created])
    return created


def review_mapping(session: Session, mapping_id: int, approve: bool, user: str, oem_code: str | None = None, region_code: str | None = None,
                   valid_from: date | None = None) -> AccountOemMapping:
    m = session.get(AccountOemMapping, mapping_id)
    if m is None or m.status != "PENDING_REVIEW":
        raise MappingError("mapping is not pending review")
    before = dict(oem_id=m.oem_id, region=m.region_code, status=m.status)
    if not approve:
        m.status, m.reviewed_by, m.reviewed_at = "REJECTED", user, datetime.utcnow()
        audit(session, user, "MAPPING_REJECT", "mapping", m.id, before, dict(status="REJECTED"))
        return m
    if oem_code:
        oem = session.execute(select(Oem).where(Oem.code == oem_code)).scalar_one_or_none()
        if oem is None:
            raise MappingError(f"unknown OEM {oem_code}")
        m.oem_id = oem.id
    if region_code:
        m.region_code = region_code
    vf = valid_from or m.valid_from
    _close_active(session, m.account_id, vf)
    m.status, m.valid_from, m.reviewed_by, m.reviewed_at = "ACTIVE", vf, user, datetime.utcnow()
    session.flush()
    audit(session, user, "MAPPING_APPROVE", "mapping", m.id, before, dict(oem_id=m.oem_id, region=m.region_code, status="ACTIVE"))
    return m


def load_mapping_table(session: Session) -> pd.DataFrame:
    rows = session.execute(
        select(AccountOemMapping.account_id, Oem.code.label("oem_code"), AccountOemMapping.region_code, AccountOemMapping.allocation_pct.label("pct"),
               AccountOemMapping.valid_from, AccountOemMapping.valid_to)
        .join(Oem, Oem.id == AccountOemMapping.oem_id).where(AccountOemMapping.status == "ACTIVE")).all()
    df = pd.DataFrame(rows, columns=["account_id", "oem_code", "region_code", "pct", "valid_from", "valid_to"])
    df["valid_from"] = pd.to_datetime(df["valid_from"])
    df["valid_to"] = pd.to_datetime(df["valid_to"])
    return df


def map_fact_frame(df: pd.DataFrame, mapping: pd.DataFrame, value_cols: list[str], month_col: str = "month",
                   account_col: str = "account_id", end_col: str | None = "end_account_id") -> pd.DataFrame:
    """Resolve source-system rows to OEM x Home Region (as-of each row's month).
    Precedence: end-customer mapping > sold-to mapping. Distributor allocations split value columns by pct.
    Unmapped rows are kept under ('UNMAPPED','UNK') so totals are always conserved."""
    d = df.copy().reset_index(drop=True)
    d["_rid"] = np.arange(len(d))
    d["_m"] = pd.to_datetime(d[month_col])
    parts = []

    def resolve(sub: pd.DataFrame, key: str) -> pd.DataFrame:
        j = sub[["_rid", "_m", key]].merge(mapping, left_on=key, right_on="account_id", how="inner", suffixes=("", "_map"))
        ok = (j["valid_from"] <= j["_m"]) & (j["valid_to"].isna() | (j["_m"] < j["valid_to"]))
        return j[ok][["_rid", "oem_code", "region_code", "pct"]]

    done = pd.DataFrame(columns=["_rid", "oem_code", "region_code", "pct"])
    if end_col and end_col in d:
        has_end = d[d[end_col].notna()].copy()
        has_end[end_col] = has_end[end_col].astype(int)
        done = resolve(has_end, end_col)
        parts.append(done)
    rest = d[~d["_rid"].isin(done["_rid"])]
    r2 = resolve(rest, account_col)
    parts.append(r2)
    got = pd.concat(parts, ignore_index=True) if parts else done
    un = d[~d["_rid"].isin(got["_rid"])][["_rid"]].assign(oem_code=UNMAPPED[0], region_code=UNMAPPED[1], pct=1.0)
    allm = pd.concat([got, un], ignore_index=True)
    out = d.drop(columns=["_m"]).merge(allm, on="_rid", how="left")
    for c in value_cols:
        out[c] = out[c] * out["pct"]
    return out.drop(columns=["_rid"])


def fx_table(session: Session) -> pd.DataFrame:
    df = pd.DataFrame(session.execute(select(FxRate.month, FxRate.currency, FxRate.rate_to_usd)).all(), columns=["month", "currency", "rate"])
    df["month"] = pd.to_datetime(df["month"])
    return df


def to_usd(df: pd.DataFrame, local_col: str, session: Session, month_col: str = "month", ccy_col: str = "currency", policy: str | None = None,
           base_month: date | None = None) -> pd.Series:
    """Convert local-currency values to USD under the configured FX policy (constant | actual)."""
    policy = policy or get_setting(session, "fx_policy")
    fx = fx_table(session)
    ccy_rates = fx.pivot(index="month", columns="currency", values="rate")
    last_actual = pd.to_datetime(df[month_col]).max()
    if policy == "constant":
        bm = base_month or get_setting(session, "fx_base_month")
        bm = pd.Timestamp(bm) if bm else last_actual
        bm = ccy_rates.index[ccy_rates.index <= bm].max()
        rate = df[ccy_col].map(ccy_rates.loc[bm].to_dict())
    else:
        idx = pd.MultiIndex.from_frame(pd.DataFrame({"m": pd.to_datetime(df[month_col]), "c": df[ccy_col]}))
        s = fx.set_index(["month", "currency"])["rate"]
        rate = pd.Series(s.reindex(idx).to_numpy(), index=df.index)
    rate = rate.fillna(1.0) if (df[ccy_col] == "USD").all() else rate
    return df[local_col] * rate.astype(float)


def materialize_mapped_series(session: Session) -> dict:
    """Rebuild mapped_series (restatement-safe: re-run after any mapping edit)."""
    sales = pd.DataFrame(session.execute(select(SalesActual.month, SalesActual.account_id, SalesActual.end_account_id, SalesActual.product_code,
                                                SalesActual.units, SalesActual.revenue_local, SalesActual.currency)).all(),
                         columns=["month", "account_id", "end_account_id", "product_code", "units", "revenue_local", "currency"])
    mapping = load_mapping_table(session)
    m = map_fact_frame(sales, mapping, ["units", "revenue_local"])
    m["revenue_usd"] = to_usd(m, "revenue_local", session)
    g = m.groupby(["month", "oem_code", "region_code", "product_code"], as_index=False)[["units", "revenue_usd"]].sum()
    session.execute(delete(MappedSeries))
    recs = [dict(month=to_month(r.month), oem_code=r.oem_code, region_code=r.region_code, product_code=r.product_code, units=float(r.units),
                 revenue_usd=float(r.revenue_usd)) for r in g.itertuples(index=False)]
    if recs:
        session.execute(insert(MappedSeries), recs)
    tot = float(sales["units"].sum())
    unm = float(g[g.oem_code == UNMAPPED[0]].units.sum())
    return dict(rows=len(recs), total_units=tot, unmapped_units=unm, unmapped_share=unm / tot if tot else 0.0)
