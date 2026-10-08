"""Forecast Value Added: does a human override beat the raw AI baseline?  FVA_Sales = wMAPE_AI - wMAPE_Consensus (positive = value added)
and FVA_AI = wMAPE_Naive - wMAPE_AI. Frozen versions only; same lead-time; bootstrap CI + significance."""
from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.core.settings_store import get_setting
from app.models.facts import MappedSeries
from app.models.governance import ConsensusPoint, FvaResult


def wmape_np(a: np.ndarray, f: np.ndarray) -> float:
    d = np.abs(a).sum()
    return float(np.abs(a - f).sum() / d) if d > 0 else float("nan")


def fva_stats(df: pd.DataFrame, rng: np.random.Generator, alpha: float = 0.05, B: int = 400) -> dict:
    a, ai, cons, nv = (df[c].to_numpy(float) for c in ("y", "ai_units", "consensus_units", "naive_units"))
    w_ai, w_c, w_n = wmape_np(a, ai), wmape_np(a, cons), wmape_np(a, nv)
    out = dict(n_obs=int(len(df)), wmape_naive=w_n, wmape_ai=w_ai, wmape_consensus=w_c, fva_sales=w_ai - w_c, fva_ai=w_n - w_ai,
               fva_sales_ci_low=None, fva_sales_ci_high=None, significant=False)
    if len(df) >= 12:
        idx = rng.integers(0, len(df), size=(B, len(df)))
        delta = np.array([wmape_np(a[i], ai[i]) - wmape_np(a[i], cons[i]) for i in idx])
        lo, hi = np.nanquantile(delta, [alpha / 2, 1 - alpha / 2])
        out.update(fva_sales_ci_low=float(lo), fva_sales_ci_high=float(hi), significant=bool(lo > 0 or hi < 0))
    return out


def compute_fva(session: Session, seed: int = 11) -> int:
    cp = pd.DataFrame(session.execute(select(ConsensusPoint.run_id, ConsensusPoint.oem_code, ConsensusPoint.region_code, ConsensusPoint.product_code,
                                             ConsensusPoint.month, ConsensusPoint.horizon, ConsensusPoint.ai_units, ConsensusPoint.consensus_units,
                                             ConsensusPoint.naive_units, ConsensusPoint.overridden, ConsensusPoint.reason_code, ConsensusPoint.user_id)).all(),
                      columns=["run_id", "oem", "region", "product", "month", "horizon", "ai_units", "consensus_units", "naive_units", "overridden", "reason_code", "user_id"])
    session.execute(delete(FvaResult))
    if cp.empty:
        return 0
    act = pd.DataFrame(session.execute(select(MappedSeries.month, MappedSeries.oem_code, MappedSeries.region_code, MappedSeries.product_code, MappedSeries.units)
                                       .where(MappedSeries.oem_code != "UNMAPPED")).all(), columns=["month", "oem", "region", "product", "y"])
    last_actual = act.month.max()
    d = cp.merge(act, on=["month", "oem", "region", "product"], how="left")
    d = d[d.month <= last_actual].copy()
    d["y"] = d["y"].fillna(0.0)  # a matured month with no mapped row means zero demand
    if d.empty:
        return 0
    alpha = float(get_setting(session, "fva_significance_level"))
    rng = np.random.default_rng(seed)
    rows = []

    def add(scope: str, frame: pd.DataFrame, horizon: int | None = None):
        if len(frame) >= 1:  # n_obs is reported; CI/significance need >= 12
            rows.append(dict(run_id="ALL", scope=scope, horizon=horizon, **fva_stats(frame, rng, alpha)))

    ov = d[d.overridden]
    add("ALL_ROWS", d)
    add("ALL", ov)
    for h, g in ov.groupby("horizon"):
        add("ALL", g, int(h))
    for dim, col in (("oem", "oem"), ("region", "region"), ("product", "product"), ("user", "user_id"), ("reason", "reason_code")):
        for v, g in ov.groupby(col):
            add(f"{dim}:{v}", g)
    for rid, g in ov.groupby("run_id"):
        rows.append(dict(run_id=rid, scope="RUN", horizon=None, **fva_stats(g, rng, alpha))) if len(g) >= 1 else None
    if rows:
        session.execute(insert(FvaResult), rows)
    return len(rows)
