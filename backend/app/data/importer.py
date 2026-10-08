"""Bring-your-own-data: map arbitrary tables onto the platform's canonical model.

Only a *sales history* table is required (month, customer, product, units, revenue|price). Region, OEM, backlog, capacity and a
customer->OEM mapping table are optional - whatever is missing simply switches the dependent features off (see
`app.core.workspace.compute_capabilities`).

Canonical identity rules
  * no OEM column, no mapping file  -> every customer is its own OEM
  * OEM / region columns in the sales table -> exact: one account per (customer, OEM, region) combination actually seen
  * mapping file (customer -> OEM [,region] [,allocation_pct]) -> distributor-style, effective from the start of history
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import current_schema
from app.mapping import service
from app.mapping.normalize import normalize_name
from app.models.facts import BacklogSnapshot, CapacityAllocation, SalesActual
from app.models.reference import Account, AccountOemMapping, FxRate, Oem, OemAlias, ProductLine, Region

log = logging.getLogger(__name__)
Progress = Callable[[float, str], None]

MAX_SERIES_DEFAULT = 600
FIELDS = {
    "sales": {"required": ["month", "customer", "product", "units"], "optional": ["revenue", "price", "region", "oem", "family"]},
    "mapping": {"required": ["customer", "oem"], "optional": ["region", "allocation_pct"]},
    "backlog": {"required": ["snapshot_month", "delivery_month", "customer", "product", "units"], "optional": ["value"]},
    "capacity": {"required": ["month", "region", "product", "capacity_units"], "optional": []},
}
ALIASES = {
    "month": ["month", "date", "period", "order_date", "invoice_date", "ship_date", "ds", "yearmonth", "year_month", "posting_date"],
    "snapshot_month": ["snapshot_month", "snapshot", "as_of", "asof", "snapshot_date"],
    "delivery_month": ["delivery_month", "delivery", "ship_month", "delivery_date", "due_month", "due_date"],
    "customer": ["customer", "sold_to", "soldto", "sold_to_name", "account", "client", "customer_name", "buyer", "store", "store_id", "dealer", "distributor"],
    "product": ["product", "sku", "item", "part", "product_code", "product_line", "material", "dept", "dept_id", "item_id"],
    "family": ["family", "category", "cat", "cat_id", "product_family", "segment"],
    "units": ["units", "qty", "quantity", "volume", "units_sold", "shipped_units", "sales_units", "sold"],
    "revenue": ["revenue", "sales", "sales_usd", "amount", "net_sales", "sales_amount", "value", "net_revenue", "invoice_amount", "sales_value", "turnover"],
    "price": ["price", "unit_price", "asp", "sell_price", "avg_price"],
    "region": ["region", "territory", "geo", "area", "state", "state_id", "country", "market"],
    "oem": ["oem", "brand", "end_customer", "parent", "parent_customer", "ultimate_parent", "group", "customer_group"],
    "allocation_pct": ["allocation_pct", "allocation", "share", "pct", "percent", "split"],
    "value": ["value", "backlog_value", "amount", "revenue"],
    "capacity_units": ["capacity_units", "capacity", "cap", "max_units"],
}


class ImportError_(ValueError):  # noqa: N801 - avoid shadowing the builtin ImportError
    pass


def _key(c: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(c).lower()).strip("_")


def suggest_mapping(role: str, columns: list[str]) -> dict[str, str | None]:
    """Best-guess {canonical field -> source column} from header names."""
    spec = FIELDS[role]
    keyed = {_key(c): c for c in columns}
    used: set[str] = set()
    out: dict[str, str | None] = {}
    for f in spec["required"] + spec["optional"]:
        pick = None
        for a in ALIASES.get(f, [f]):
            if a in keyed and keyed[a] not in used:
                pick = keyed[a]
                break
        if pick is None:
            for a in ALIASES.get(f, [f]):
                hit = next((orig for k, orig in keyed.items() if a in k and orig not in used), None)
                if hit:
                    pick = hit
                    break
        if pick:
            used.add(pick)
        out[f] = pick
    return out


def read_table(path: str | Path, nrows: int | None = None) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        raise ImportError_(f"file not found: {p.name}")
    ext = p.suffix.lower()
    if ext in (".parquet", ".pq"):
        df = pd.read_parquet(p)
        return df.head(nrows) if nrows else df
    if ext in (".csv", ".txt", ".tsv", ".gz"):
        return pd.read_csv(p, sep=None, engine="python", nrows=nrows) if ext == ".tsv" or nrows else pd.read_csv(p, low_memory=False)
    raise ImportError_(f"unsupported file type '{ext}' (use .csv or .parquet)")


def _code(s: str, maxlen: int) -> str:
    c = re.sub(r"[^A-Z0-9_\-]+", "_", str(s).strip().upper()).strip("_")[:maxlen] or "X"
    return "ALL_" if c == "ALL" else c


def _unique_codes(values: list[str], maxlen: int) -> dict[str, str]:
    out, used = {}, set()
    for v in values:
        c = _code(v, maxlen)
        base, n = c, 1
        while c in used:
            n += 1
            sfx = f"_{n}"
            c = base[: maxlen - len(sfx)] + sfx
        used.add(c)
        out[v] = c
    return out


def _col(df: pd.DataFrame, m: dict, field: str, required: bool = False):
    src = m.get(field)
    if not src:
        if required:
            raise ImportError_(f"column for '{field}' is not mapped")
        return None
    if src not in df.columns:
        raise ImportError_(f"mapped column '{src}' (for {field}) is not in the file")
    return df[src]


# --------------------------------------------------------------------------------------------- sales
def canonical_sales(df: pd.DataFrame, colmap: dict, opts: dict) -> tuple[pd.DataFrame, list[dict]]:
    """Source table -> canonical frame [month, customer, product, region, oem, family, units, revenue] + issues."""
    issues: list[dict] = []

    def add(level, msg):
        issues.append(dict(level=level, message=msg))

    out = pd.DataFrame(index=df.index)
    out["customer"] = _col(df, colmap, "customer", True).astype(str).str.strip()
    out["product"] = _col(df, colmap, "product", True).astype(str).str.strip()
    raw_date = _col(df, colmap, "month", True)
    dt = pd.to_datetime(raw_date, errors="coerce", format=opts.get("date_format") or None)
    bad = int(dt.isna().sum())
    if bad:
        (add("error" if bad > 0.05 * len(df) else "warn", f"{bad:,} rows have an unreadable date and {'make the file unusable' if bad > 0.05 * len(df) else 'were dropped'} (example: {raw_date[dt.isna()].iloc[0]!r})"))
    out["_dt"] = dt
    out["units"] = pd.to_numeric(_col(df, colmap, "units", True), errors="coerce")
    nbad = int(out.units.isna().sum())
    if nbad:
        add("warn", f"{nbad:,} rows have non-numeric units and were dropped")
    rev, price = _col(df, colmap, "revenue"), _col(df, colmap, "price")
    if rev is not None:
        out["revenue"] = pd.to_numeric(rev, errors="coerce")
    elif price is not None:
        out["revenue"] = out.units * pd.to_numeric(price, errors="coerce")
        add("info", "revenue = units x the mapped price column")
    elif opts.get("constant_price"):
        out["revenue"] = out.units * float(opts["constant_price"])
        add("warn", f"no revenue/price column - using a constant price of {float(opts['constant_price']):,.2f} (ASP will be flat)")
    else:
        add("error", "no revenue or price column mapped - map one, or set a constant price")
        out["revenue"] = np.nan
    out["revenue"] = out["revenue"].fillna(0.0) if rev is None else out["revenue"]
    region, oem, fam = _col(df, colmap, "region"), _col(df, colmap, "oem"), _col(df, colmap, "family")
    out["region"] = region.astype(str).str.strip() if region is not None else opts.get("default_region", "Global")
    out["oem"] = oem.astype(str).str.strip() if oem is not None else out["customer"]
    out["family"] = fam.astype(str).str.strip() if fam is not None else "All"
    out = out[out._dt.notna() & out.units.notna() & out.revenue.notna()]
    out = out[(out.customer != "") & (out["product"] != "")]
    if out.empty:
        add("error", "no usable rows after cleaning")
        return out.assign(month=pd.NaT), issues
    # sub-monthly data -> months; a trailing partial month would look like a collapse, so drop it
    sub = bool((out._dt.dt.day != 1).any())
    out["month"] = out._dt.dt.to_period("M").dt.to_timestamp()
    if sub:
        last = out._dt.max()
        if last != last + pd.offsets.MonthEnd(0):
            drop = out.month == out.month.max()
            out = out[~drop]
            add("info", f"daily/weekly data aggregated to months; the incomplete last month ({last:%Y-%m}, up to {last:%d}) was dropped")
        else:
            add("info", "daily/weekly data aggregated to months")
    out = out.drop(columns="_dt")
    if (out.units < 0).any():
        add("warn", f"{int((out.units < 0).sum()):,} rows have negative units (returns?) - kept as-is; consider netting upstream")
    return out, issues


def summarize_sales(c: pd.DataFrame, issues: list[dict], max_series: int) -> dict:
    months = sorted(c.month.unique())
    bottoms = c.groupby(["oem", "region", "product"]).ngroups
    if len(months) < 18:
        issues.append(dict(level="error", message=f"only {len(months)} months of history - at least 18 are required (30+ recommended)"))
    elif len(months) < 30:
        issues.append(dict(level="warn", message=f"{len(months)} months of history; 30+ gives noticeably better seasonality and backtests"))
    if bottoms > max_series:
        issues.append(dict(level="error", message=f"{bottoms:,} OEM x region x product series exceeds the limit of {max_series:,}; aggregate (e.g. product family instead of SKU) or filter the file"))
    elif bottoms > max_series // 2:
        issues.append(dict(level="warn", message=f"{bottoms:,} series - forecasting will be slow; consider aggregating"))
    if (c.revenue <= 0).mean() > 0.5:
        issues.append(dict(level="warn", message="more than half of the rows have zero revenue - check the revenue/price mapping"))
    dup = int(c.duplicated(["month", "customer", "product", "region", "oem"]).sum())
    if dup:
        issues.append(dict(level="info", message=f"{dup:,} duplicate month/customer/product rows will be summed"))
    return dict(rows=int(len(c)), months=len(months), first_month=str(pd.Timestamp(months[0]).date()) if months else None, last_month=str(pd.Timestamp(months[-1]).date()) if months else None,
                customers=int(c.customer.nunique()), oems=int(c.oem.nunique()), regions=int(c.region.nunique()), products=int(c["product"].nunique()), series=int(bottoms),
                units_total=float(c.units.sum()), revenue_total=float(c.revenue.sum()))


def validate_sales(path: str | Path, colmap: dict, opts: dict | None = None) -> dict:
    opts = opts or {}
    df = read_table(path)
    c, issues = canonical_sales(df, colmap, opts)
    summary = summarize_sales(c, issues, int(opts.get("max_series", MAX_SERIES_DEFAULT))) if len(c) else {}
    return dict(ok=not any(i["level"] == "error" for i in issues), issues=issues, summary=summary, preview=c.head(8).astype(str).to_dict("records"))


# --------------------------------------------------------------------------------------------- load
def _insert_chunks(session: Session, model, records: list[dict], size: int = 20000) -> None:
    for i in range(0, len(records), size):
        session.execute(insert(model), records[i:i + size])


def load_canonical(session: Session, c: pd.DataFrame, opts: dict, user: str, progress: Progress, mapping_df: pd.DataFrame | None = None,
                   backlog: pd.DataFrame | None = None, capacity: pd.DataFrame | None = None) -> dict:
    """Write a canonical sales frame (+ optional tables) into the active, already-wiped workspace."""
    from app.core.settings_store import seed_defaults
    from app.mapping.rules import seed_default_rules
    from app.risk.engine import seed_default_thresholds

    seed_defaults(session)
    seed_default_rules(session)
    seed_default_thresholds(session)
    progress(0.05, "Building reference data")
    c = c.groupby(["month", "customer", "oem", "region", "product", "family"], as_index=False)[["units", "revenue"]].sum()
    rcodes = _unique_codes(sorted(c.region.unique()), 8)
    pcodes = _unique_codes(sorted(c["product"].unique()), 32)
    session.execute(insert(Region), [dict(code=rcodes[r], name=r) for r in rcodes])
    fam = c.groupby("product").family.agg(lambda s: s.mode().iloc[0]).to_dict()
    session.execute(insert(ProductLine), [dict(code=pcodes[p], name=p, family=fam.get(p, "All"), lead_time_months=2) for p in pcodes])
    c["pcode"], c["rcode"] = c["product"].map(pcodes), c.region.map(rcodes)

    # ---- who maps to which OEM
    if mapping_df is not None:
        md = mapping_df.copy()
        md["region"] = md["region"].astype(str).str.strip() if "region" in md else opts.get("default_region", "Global")
        md["allocation_pct"] = pd.to_numeric(md.get("allocation_pct", 1.0), errors="coerce").fillna(1.0)
        extra = sorted(set(md.region) - set(rcodes))
        if extra:
            rcodes.update(_unique_codes([e for e in extra], 8))
            session.execute(insert(Region), [dict(code=rcodes[r], name=r) for r in extra])
        oem_names = sorted(set(md.oem.astype(str)))
        acct_keys = sorted(c.customer.unique())
        c["acct_key"] = c.customer
    else:
        oem_names = sorted(c.oem.unique())
        multi = c.drop_duplicates(["customer", "oem", "region"]).groupby("customer").size()
        c["acct_key"] = np.where(c.customer.map(multi) > 1, c.customer + " → " + c.oem + " · " + c.region, c.customer)
        acct_keys = sorted(c.acct_key.unique())
    ocodes = _unique_codes(oem_names, 32)
    session.execute(insert(Oem), [dict(code=ocodes[o], name=o, is_active=True) for o in oem_names])
    session.flush()
    oem_id = {o.code: o.id for o in session.query(Oem)}
    session.execute(insert(OemAlias), [dict(oem_id=oem_id[ocodes[o]], alias=o, normalized_alias=normalize_name(o)) for o in oem_names])
    acct_id: dict[str, int] = {}
    recs = []
    for i, k in enumerate(acct_keys, start=1):
        acct_id[k] = i
        recs.append(dict(id=i, erp_customer_id=f"C{i:06d}", name=k[:200], normalized_name=normalize_name(k)[:200], account_type="DIRECT", is_active=True))
    session.execute(insert(Account), recs)
    session.flush()

    maps = []
    if mapping_df is not None:
        grp = md.groupby("customer")
        for cust, g in grp:
            if cust not in acct_id:
                continue
            tot = float(g.allocation_pct.sum()) or 1.0
            for r in g.itertuples():
                maps.append(dict(account_id=acct_id[cust], oem_id=oem_id[ocodes[str(r.oem)]], region_code=rcodes[r.region], allocation_pct=float(r.allocation_pct) / tot,
                                 source="IMPORT", confidence=1.0, status="ACTIVE", created_by=user, evidence={"reason": "customer -> OEM mapping file"}))
            if len(g) > 1:
                session.query(Account).filter(Account.id == acct_id[cust]).update({"account_type": "DISTRIBUTOR"})
    else:
        pairs = c[["acct_key", "oem", "rcode"]].drop_duplicates("acct_key")
        for r in pairs.itertuples():
            maps.append(dict(account_id=acct_id[r.acct_key], oem_id=oem_id[ocodes[r.oem]], region_code=r.rcode, allocation_pct=1.0, source="IMPORT",
                             confidence=1.0, status="ACTIVE", created_by=user, evidence={"reason": "imported sales file"}))
    _insert_chunks(session, AccountOemMapping, maps)

    months = sorted(c.month.unique())
    session.execute(insert(FxRate), [dict(month=pd.Timestamp(m).date(), currency="USD", rate_to_usd=1.0) for m in months])
    progress(0.20, f"Loading {len(c):,} sales rows")
    c["account_id"] = c.acct_key.map(acct_id)
    s = c.groupby(["month", "account_id", "pcode"], as_index=False)[["units", "revenue"]].sum()
    _insert_chunks(session, SalesActual, [dict(month=pd.Timestamp(r.month).date(), account_id=int(r.account_id), end_account_id=None, product_code=r.pcode,
                                              units=float(r.units), revenue_local=float(r.revenue), currency="USD") for r in s.itertuples(index=False)])
    n_bl = n_cap = 0
    if backlog is not None and len(backlog):
        progress(0.40, "Loading backlog")
        b = backlog.copy()
        b["account_id"] = b.customer.map(lambda x: acct_id.get(x))
        b["pcode"] = b["product"].map(pcodes)
        b = b[b.account_id.notna() & b.pcode.notna()]
        n_bl = len(b)
        _insert_chunks(session, BacklogSnapshot, [dict(snapshot_month=pd.Timestamp(r.snapshot_month).date(), delivery_month=pd.Timestamp(r.delivery_month).date(), account_id=int(r.account_id),
                                                      end_account_id=None, product_code=r.pcode, units=float(r.units), value_local=float(r.value), currency="USD")
                                                 for r in b.itertuples(index=False)])
    if capacity is not None and len(capacity):
        cp = capacity.copy()
        cp["rcode"], cp["pcode"] = cp.region.astype(str).map(rcodes), cp["product"].astype(str).map(pcodes)
        cp = cp[cp.rcode.notna() & cp.pcode.notna()].groupby([pd.to_datetime(cp.month).dt.to_period("M").dt.to_timestamp(), "rcode", "pcode"], as_index=False).capacity_units.sum()
        n_cap = len(cp)
        session.execute(insert(CapacityAllocation), [dict(month=pd.Timestamp(r.month).date(), region_code=r.rcode, product_code=r.pcode, capacity_units=float(r.capacity_units))
                                                      for r in cp.itertuples(index=False)])
    session.flush()
    progress(0.55, "Materialising OEM x region x product history")
    mat = service.materialize_mapped_series(session)
    return dict(accounts=len(acct_keys), oems=len(oem_names), regions=len(rcodes), products=len(pcodes), months=len(months), sales_rows=len(s), backlog_rows=n_bl,
                capacity_rows=n_cap, materialized=mat)


def _read_role(path_info: dict | None, role: str, base: Path) -> pd.DataFrame | None:
    """Read an optional file and rename its columns to the canonical field names."""
    if not path_info:
        return None
    df = read_table(base / path_info["file"])
    cm = path_info.get("map") or suggest_mapping(role, list(df.columns))
    missing = [f for f in FIELDS[role]["required"] if not cm.get(f)]
    if missing:
        raise ImportError_(f"{role} file: map the column(s) {', '.join(missing)}")
    out = pd.DataFrame({f: df[src] for f, src in cm.items() if src})
    for c in ("snapshot_month", "delivery_month", "month"):
        if c in out:
            out[c] = pd.to_datetime(out[c], errors="coerce").dt.to_period("M").dt.to_timestamp()
    if role == "backlog":
        out["customer"] = out.customer.astype(str).str.strip()
        out["product"] = out["product"].astype(str).str.strip()
        out["units"] = pd.to_numeric(out.units, errors="coerce").fillna(0.0)
        out["value"] = pd.to_numeric(out.get("value", out.units), errors="coerce").fillna(0.0)
    if role == "mapping":
        out["customer"], out["oem"] = out.customer.astype(str).str.strip(), out.oem.astype(str).str.strip()
    return out


def upload_dir(workspace_id: str) -> Path:
    d = get_settings().import_dir / workspace_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_import(session: Session, params: dict, user: str, progress: Progress) -> dict:
    """Job handler: replace the active workspace's data with an imported dataset, then (optionally) forecast it."""
    from app.core.workspace import workspace_kind
    from app.data.loader import wipe_domain_data

    opts = dict(params.get("options") or {})
    src = params.get("source", "files")
    progress(0.01, "Reading files")
    if src == "m5":
        from app.data.m5 import load_m5

        c, issues, extra = load_m5(Path(params["m5_dir"]) if params.get("m5_dir") else get_settings().import_dir / "m5", opts, progress)
        mapping_df = backlog = capacity = None
    else:
        base = upload_dir(params["workspace_id"])
        s = params["sales"]
        c, issues = canonical_sales(read_table(base / s["file"]), s.get("map") or {}, opts)
        mapping_df = _read_role(params.get("mapping"), "mapping", base)
        backlog = _read_role(params.get("backlog"), "backlog", base)
        capacity = _read_role(params.get("capacity"), "capacity", base)
        extra = {}
    summary = summarize_sales(c, issues, int(opts.get("max_series", MAX_SERIES_DEFAULT))) if len(c) else {}
    errs = [i["message"] for i in issues if i["level"] == "error"]
    if errs:
        raise ImportError_("; ".join(errs))
    progress(0.03, "Clearing the workspace")
    wipe_domain_data(session)
    loaded = load_canonical(session, c, opts, user, progress, mapping_df, backlog, capacity)
    session.commit()
    out = dict(summary=summary, issues=issues, loaded=loaded, kind=workspace_kind(session), **extra)
    last = pd.Timestamp(summary["last_month"]).date()
    if opts.get("run_forecast", True):
        from app.forecasting.pipeline import run_forecast
        from app.governance import cycles
        from app.quality.checks import run_dq

        run_dq(session, last)
        session.commit()
        progress(0.62, "Forecasting")
        rid = run_forecast(session, last, kind="CURRENT", mode=opts.get("forecast_mode", "fast"), user=user, progress=lambda f, m: progress(0.62 + 0.36 * f, m))
        cycles.attach_run(session, rid)
        session.commit()
        out["run_id"] = rid
    progress(0.99, "Done")
    return out
