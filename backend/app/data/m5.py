"""M5 (Walmart) preset: unit sales of 3,049 items in 10 stores / 3 states, daily, 2011-01-29 -> 2016-06-19.

Mapping onto the platform hierarchy:   store -> "OEM"   state -> region   department -> product   (revenue = units x weekly sell price)
M5 has no distributors, CRM pipeline, backlog or capacity, so only the forecasting / reconciliation / revenue / governance features apply.
Files expected in the import folder (Kaggle "M5 Forecasting - Accuracy"): sales_train_evaluation.csv (or _validation), calendar.csv, sell_prices.csv"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd

Progress = Callable[[float, str], None]
STATE_NAMES = {"CA": "California", "TX": "Texas", "WI": "Wisconsin"}


def m5_files(d: Path) -> dict[str, Path | None]:
    sales = next((d / n for n in ("sales_train_evaluation.csv", "sales_train_validation.csv") if (d / n).exists()), None)
    return dict(sales=sales, calendar=(d / "calendar.csv") if (d / "calendar.csv").exists() else None, prices=(d / "sell_prices.csv") if (d / "sell_prices.csv").exists() else None)


def m5_available(d: Path) -> dict:
    f = m5_files(d)
    return dict(available=all(f.values()), missing=[k for k, v in f.items() if v is None], folder=str(d))


def load_m5(d: Path, opts: dict, progress: Progress) -> tuple[pd.DataFrame, list[dict], dict]:
    from app.data.importer import ImportError_

    f = m5_files(d)
    if not all(f.values()):
        miss = [k for k, v in f.items() if v is None]
        raise ImportError_(f"M5 files missing in {d}: {', '.join(miss)} (need sales_train_*.csv, calendar.csv, sell_prices.csv)")
    level = opts.get("m5_product_level", "dept_id")  # dept_id (7) | cat_id (3) | item_id (thousands - use limit_items)
    limit_items = int(opts.get("limit_items") or 0)
    progress(0.02, "Reading M5 calendar and prices")
    cal = pd.read_csv(f["calendar"], usecols=["d", "date", "wm_yr_wk"])
    cal["date"] = pd.to_datetime(cal["date"])
    prices = pd.read_csv(f["prices"], dtype={"store_id": "category", "item_id": "category"})
    head = pd.read_csv(f["sales"], nrows=0).columns
    dcols = [c for c in head if c.startswith("d_")]
    dtypes = {c: "int32" for c in dcols}
    progress(0.04, "Reading M5 unit sales")
    sales = pd.read_csv(f["sales"], dtype=dtypes)
    if limit_items:
        keep = sales.item_id.drop_duplicates().head(limit_items)
        sales = sales[sales.item_id.isin(keep)]
    dmap = cal.set_index("d")
    parts = []
    stores = sorted(sales.store_id.unique())
    for i, st in enumerate(stores):
        progress(0.05 + 0.15 * i / len(stores), f"Aggregating store {st}")
        sub = sales[sales.store_id == st]
        ids = sub[["item_id", "dept_id", "cat_id", "state_id"]].reset_index(drop=True)
        long = sub[dcols].reset_index(drop=True).T
        long.columns = range(len(ids))
        long = long.stack().rename("units").reset_index()
        long.columns = ["d", "row", "units"]
        long = long.join(ids, on="row").join(dmap[["date", "wm_yr_wk"]], on="d")
        p = prices[prices.store_id == st][["item_id", "wm_yr_wk", "sell_price"]]
        p["item_id"] = p["item_id"].astype(str)
        long["item_id"] = long["item_id"].astype(str)
        long = long.merge(p, on=["item_id", "wm_yr_wk"], how="left")
        long["revenue"] = long.units * long.sell_price.fillna(0.0)
        long["month"] = long["date"].dt.to_period("M").dt.to_timestamp()
        g = long.groupby(["state_id", "dept_id", "cat_id", "item_id", "month"] if level == "item_id" else ["state_id", level, "cat_id", "month"], as_index=False)[["units", "revenue"]].sum()
        g["customer"] = st
        parts.append(g)
    c = pd.concat(parts, ignore_index=True)
    c = c.rename(columns={level: "product", "state_id": "region_code", "cat_id": "family"})
    c["region"] = c.region_code.map(lambda s: STATE_NAMES.get(s, s))
    c["oem"] = c["customer"]
    last = cal[cal.d.isin(dcols)].date.max()
    issues = [dict(level="info", message="M5: store -> OEM, state -> region, department -> product; revenue = units x weekly sell price")]
    if last != last + pd.offsets.MonthEnd(0):
        c = c[c.month != c.month.max()]
        issues.append(dict(level="info", message=f"M5 ends on {last:%Y-%m-%d}; the incomplete last month was dropped"))
    return c[["month", "customer", "oem", "region", "product", "family", "units", "revenue"]], issues, dict(m5=dict(items=int(sales.item_id.nunique()), stores=len(stores), level=level))
