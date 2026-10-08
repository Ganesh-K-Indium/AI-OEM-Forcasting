"""Consensus builder: AI baseline (immutable) + sales overrides -> final plan, coherent by construction.

Overrides may sit at any hierarchy node. Resolution is finest-first: a bottom-level override pins that leaf; a coarser override
(e.g. OEM x Region) is spread across the leaves that are not already pinned, proportionally to the AI forecast, so the node total
matches the override exactly and every finer decision is preserved. Infeasible combinations are reported as conflicts."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings_store import get_setting
from app.ml.hierarchy import ALL, level_of
from app.models.forecast import ForecastPoint
from app.models.governance import Override

DEPTH = {"BOTTOM": 3, "OEM_REGION": 2, "REGION": 1, "OEM": 1, "PRODUCT": 1, "TOTAL": 0}


@dataclass
class ConsensusResult:
    bottom: pd.DataFrame  # oem, region, product, month, horizon, ai_units, consensus_units, asp, overridden, reason_code, user_id
    conflicts: list[dict] = field(default_factory=list)
    applied: list[int] = field(default_factory=list)


def load_ai_bottom(session: Session, run_id: str) -> pd.DataFrame:
    rows = session.execute(select(ForecastPoint.oem_code, ForecastPoint.region_code, ForecastPoint.product_code, ForecastPoint.month, ForecastPoint.horizon,
                                  ForecastPoint.units_p50, ForecastPoint.asp_usd, ForecastPoint.units_p10, ForecastPoint.units_p90)
                           .where(ForecastPoint.run_id == run_id, ForecastPoint.level == "BOTTOM")).all()
    df = pd.DataFrame(rows, columns=["oem", "region", "product", "month", "horizon", "ai_units", "asp", "p10", "p90"])
    return df


def active_overrides(session: Session, run_id: str) -> list[Override]:
    q = select(Override).where(Override.run_id == run_id, Override.status == "ACTIVE", Override.approval_status != "REJECTED").order_by(Override.id)
    ovs = list(session.execute(q).scalars())
    if get_setting(session, "override_requires_approval"):
        ovs = [o for o in ovs if o.approval_status == "APPROVED"]
    return ovs


def _matches(o: Override, b: pd.Series) -> bool:
    return o.oem_code in (ALL, b.oem) and o.region_code in (ALL, b.region) and o.product_code in (ALL, b["product"])


def build_consensus(session: Session, run_id: str, ai: pd.DataFrame | None = None, overrides: list[Override] | None = None) -> ConsensusResult:
    ai = load_ai_bottom(session, run_id) if ai is None else ai
    ovs = active_overrides(session, run_id) if overrides is None else overrides
    out = ai.copy()
    out["consensus_units"] = out["ai_units"]
    out["overridden"], out["reason_code"], out["user_id"] = False, None, None
    res = ConsensusResult(out)
    for month, g in out.groupby("month"):
        mo = [o for o in ovs if o.month == month]
        if not mo:
            continue
        idx = g.index
        pinned: set[int] = set()
        for o in sorted(mo, key=lambda x: (-DEPTH.get(level_of(x.oem_code, x.region_code, x.product_code), 0), x.id)):
            leaves = [i for i in idx if _matches(o, out.loc[i])]
            if not leaves:
                res.conflicts.append(dict(override_id=o.id, reason="no leaves under node"))
                continue
            free = [i for i in leaves if i not in pinned]
            resid = o.override_units - float(out.loc[[i for i in leaves if i in pinned], "consensus_units"].sum())
            if not free:
                if abs(resid) > 1e-6:
                    res.conflicts.append(dict(override_id=o.id, reason="all leaves pinned by finer overrides; totals differ", residual=resid))
                continue
            if resid < -1e-9:
                res.conflicts.append(dict(override_id=o.id, reason="override total is below the sum of finer-level overrides", residual=resid))
                continue
            w = out.loc[free, "ai_units"].to_numpy()
            w = w / w.sum() if w.sum() > 1e-12 else np.full(len(free), 1 / len(free))
            out.loc[free, "consensus_units"] = resid * w
            out.loc[free, "overridden"] = True
            out.loc[free, "reason_code"] = o.reason_code
            out.loc[free, "user_id"] = o.user_id
            pinned |= set(free)
            res.applied.append(o.id)
    out["ai_revenue"] = out.ai_units * out.asp
    out["consensus_revenue"] = out.consensus_units * out.asp
    return res


def rollup(df: pd.DataFrame, value_cols: list[str]) -> pd.DataFrame:
    """Bottom frame -> all hierarchy levels (oem_code/region_code/product_code with 'ALL' for aggregates)."""
    parts = []
    spec = {"TOTAL": [], "REGION": ["region"], "OEM": ["oem"], "PRODUCT": ["product"], "OEM_REGION": ["oem", "region"], "BOTTOM": ["oem", "region", "product"]}
    for level, keys in spec.items():
        g = df.groupby(keys + ["month", "horizon"], as_index=False)[value_cols].sum() if keys else df.groupby(["month", "horizon"], as_index=False)[value_cols].sum()
        for c, name in (("oem", "oem_code"), ("region", "region_code"), ("product", "product_code")):
            g[name] = g[c] if c in keys else ALL
        g["level"] = level
        parts.append(g[["level", "oem_code", "region_code", "product_code", "month", "horizon"] + value_cols])
    return pd.concat(parts, ignore_index=True)
