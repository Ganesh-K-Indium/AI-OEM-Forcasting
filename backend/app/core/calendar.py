"""Month-grain calendar helpers (all months are normalised to the 1st)."""
from __future__ import annotations

from datetime import date

import pandas as pd


def to_month(d: date | pd.Timestamp | str) -> date:
    ts = pd.Timestamp(d)
    return date(ts.year, ts.month, 1)


def add_months(d: date, n: int) -> date:
    idx = d.year * 12 + (d.month - 1) + n
    return date(idx // 12, idx % 12 + 1, 1)


def month_range(start: date, end: date) -> list[date]:
    """Inclusive list of month starts."""
    out, cur = [], to_month(start)
    end = to_month(end)
    while cur <= end:
        out.append(cur)
        cur = add_months(cur, 1)
    return out


def months_between(a: date, b: date) -> int:
    """Number of months from a to b (b - a)."""
    return (b.year - a.year) * 12 + (b.month - a.month)


def is_quarter_end(d: date) -> bool:
    return d.month in (3, 6, 9, 12)
