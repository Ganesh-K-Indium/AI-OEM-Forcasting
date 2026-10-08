"""Demo bootstrap: synthetic data -> mapping -> DQ -> historical replay cycles (with simulated rep overrides) -> current cycle -> FVA/risk."""
from __future__ import annotations

import logging
from collections.abc import Callable

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.calendar import add_months
from app.core.config import get_settings
from app.core.db import Base, engine
from app.data import loader
from app.data.synthetic import generate
from app.forecasting.pipeline import run_forecast
from app.governance import cycles, fva, overrides
from app.mapping import service as mapping_service
from app.models.facts import MappedSeries
from app.models.forecast import ForecastPoint
from app.models.ops import User
from app.quality.checks import run_dq
from app.risk.engine import refresh_alerts, seed_default_thresholds

log = logging.getLogger(__name__)
REGION_BIAS = {"AMER": 0.05, "EMEA": -0.02, "APAC": 0.08}
REASONS = {"CUSTOMER_DIRECT_GUIDANCE": 0.50, "PROJECT_DELAY": 0.35, "NEW_WIN": 0.20, "CAPACITY_CAP": 0.40}  # reason -> share of the true gap the rep "knows"
REP_USER = {"AMER": "rep.amer@demo.local", "EMEA": "rep.emea@demo.local", "APAC": "rep.apac@demo.local"}


def simulate_overrides(session: Session, run_id: str, rng: np.random.Generator, with_foresight: bool, share: float = 0.35, max_h: int = 3) -> int:
    """Synthetic rep behaviour: optimism bias by region + partial foresight of the true outcome (replay only)."""
    fp = pd.DataFrame(session.execute(select(ForecastPoint.oem_code, ForecastPoint.region_code, ForecastPoint.month, ForecastPoint.horizon, ForecastPoint.units_p50)
                                      .where(ForecastPoint.run_id == run_id, ForecastPoint.level == "OEM_REGION", ForecastPoint.horizon <= max_h)).all(),
                      columns=["oem", "region", "month", "horizon", "ai"])
    act = pd.DataFrame(session.execute(select(MappedSeries.month, MappedSeries.oem_code, MappedSeries.region_code, MappedSeries.units)
                                       .where(MappedSeries.oem_code != "UNMAPPED")).all(), columns=["month", "oem", "region", "u"]).groupby(["month", "oem", "region"], as_index=False).u.sum()
    fp = fp.merge(act, on=["month", "oem", "region"], how="left")
    users = {r: session.execute(select(User).where(User.email == e)).scalar_one() for r, e in REP_USER.items()}
    n = 0
    for r in fp.sample(frac=share, random_state=int(rng.integers(1 << 30))).itertuples(index=False):
        reason = rng.choice(list(REASONS), p=[0.35, 0.30, 0.20, 0.15])
        alpha = REASONS[reason] if with_foresight and np.isfinite(r.u) else 0.0
        target = r.ai + (alpha * (r.u - r.ai) if np.isfinite(r.u) else 0.0)
        val = max(0.0, target + REGION_BIAS[r.region] * r.ai + rng.normal(0, 0.04) * r.ai)
        try:
            overrides.create_override(session, users[r.region], run_id, r.oem, r.region, "ALL", r.month, "UNITS", float(val), str(reason),
                                      f"[synthetic] rep view on {r.oem} {r.region}", synthetic=True, enforce_scope=False)
            n += 1
        except overrides.OverrideError:
            continue
    return n


def seed_demo(session: Session, progress: Callable[[float, str], None] | None = None, replay_cycles: int = 6, fast: bool = False) -> dict:
    cfg = get_settings()
    pg = progress or (lambda f, m: None)
    Base.metadata.create_all(engine)
    pg(0.02, "Generating synthetic enterprise data")
    loader.wipe_domain_data(session)
    b = generate(cfg.synth_seed, cfg.synth_end_month, cfg.synth_months, cfg.synth_opportunities)
    loader.ensure_demo_users(session)
    stats = loader.load_bundle(session, b)
    seed_default_thresholds(session)
    session.commit()
    pg(0.08, "Entity resolution (rules + fuzzy + steward review)")
    mres = mapping_service.run_mapping_pipeline(session, "seed")
    sres = loader.steward_replay(session, b)
    mat = mapping_service.materialize_mapped_series(session)
    session.commit()
    last = cfg.synth_end_month
    run_dq(session, last)
    session.commit()
    rng = np.random.default_rng(cfg.random_seed)
    replay_ids = []
    cycles_list = [add_months(last, -k) for k in range(replay_cycles, 0, -1)]
    for i, cm in enumerate(cycles_list):
        pg(0.12 + 0.50 * i / max(len(cycles_list), 1), f"Historical replay cycle {cm:%Y-%m}")
        rid = run_forecast(session, cm, kind="REPLAY", mode="fast", user="seed")
        simulate_overrides(session, rid, rng, with_foresight=True)
        cycles.lock_run(session, rid, "seed")
        session.commit()
        replay_ids.append(rid)
    pg(0.65, "Current cycle (full model set)")
    rid = run_forecast(session, last, kind="CURRENT", mode="fast" if fast else "full", user="seed",
                       progress=lambda f, m: pg(0.65 + 0.28 * f, m))
    cycles.attach_run(session, rid)
    n_open = simulate_overrides(session, rid, rng, with_foresight=False, share=0.15, max_h=2)
    cycles.advance_to_consensus(session, last, "seed")
    refresh_alerts(session, rid)
    pg(0.96, "FVA")
    nf = fva.compute_fva(session)
    session.commit()
    pg(1.0, "Seed complete")
    return dict(load=stats, mapping=mres, steward=sres, materialized=mat, replay_runs=replay_ids, current_run=rid, open_overrides=n_open, fva_rows=nf)
