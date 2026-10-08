"""Model / data drift monitors: PSI of recent vs reference demand distribution, and live error vs backtest error."""
from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.models.ops import DriftReport

PSI_ALERT = 0.25
ERROR_RATIO_ALERT = 1.30


def psi(ref: np.ndarray, cur: np.ndarray, bins: int = 8) -> float:
    ref, cur = np.asarray(ref, float), np.asarray(cur, float)
    if len(ref) < 8 or len(cur) < 4:
        return 0.0
    edges = np.unique(np.quantile(ref, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:
        return 0.0
    edges[0], edges[-1] = -np.inf, np.inf
    r = np.histogram(ref, edges)[0] / len(ref)
    c = np.histogram(cur, edges)[0] / len(cur)
    r, c = np.clip(r, 1e-4, None), np.clip(c, 1e-4, None)
    return float(((c - r) * np.log(c / r)).sum())


def feature_drift(session: Session, run_id: str, units_wide: pd.DataFrame, recent: int = 12) -> list[dict]:
    """PSI per product of per-series scaled demand: last `recent` months vs the earlier history."""
    out = []
    prods = sorted({c.split("|")[2] for c in units_wide.columns})
    for p in prods:
        cols = [c for c in units_wide.columns if c.split("|")[2] == p]
        scaled = units_wide[cols] / units_wide[cols].mean().clip(lower=1e-9)
        ref, cur = scaled.iloc[:-recent].to_numpy().ravel(), scaled.iloc[-recent:].to_numpy().ravel()
        v = psi(ref, cur)
        out.append(dict(kind="FEATURE", name=f"psi:{p}", value=v, threshold=PSI_ALERT, breached=v > PSI_ALERT))
    for o in out:
        session.add(DriftReport(run_id=run_id, **o))
    return out


def error_drift(session: Session, run_id: str, live_wmape: float | None, backtest_wmape: float | None) -> dict | None:
    if not live_wmape or not backtest_wmape:
        return None
    ratio = live_wmape / backtest_wmape
    d = dict(kind="ERROR", name="live_vs_backtest_wmape", value=float(ratio), threshold=ERROR_RATIO_ALERT, breached=ratio > ERROR_RATIO_ALERT,
             detail={"live": live_wmape, "backtest": backtest_wmape})
    session.add(DriftReport(run_id=run_id, **d))
    return d
