import pytest
from sqlalchemy import select

from app.core.audit import audit, verify_chain
from app.governance import consensus, cycles, fva, overrides
from app.models.facts import MappedSeries
from app.models.forecast import ForecastPoint
from app.models.governance import AuditLog, ConsensusPoint, FvaResult, Override
from tests.helpers import ASP, MONTHS, UNITS, mini_run, planner, rep


def cons(s, run="run-1"):
    return consensus.build_consensus(s, run)


def test_override_sets_node_total_and_never_touches_ai(fresh_db):
    s = fresh_db
    mini_run(s)
    before = [(f.id, f.units_p50) for f in s.execute(select(ForecastPoint)).scalars()]
    ov = overrides.create_override(s, planner(), "run-1", "A", "X", "ALL", MONTHS[0], "UNITS", 160.0, "NEW_WIN", None)
    res = cons(s)
    b = res.bottom
    a_x = b[(b.oem == "A") & (b.region == "X") & (b.month == MONTHS[0])]
    assert a_x.consensus_units.sum() == pytest.approx(160.0)
    assert a_x.ai_units.sum() == pytest.approx(100.0)
    other = b[~((b.oem == "A") & (b.region == "X") & (b.month == MONTHS[0]))]
    assert (other.consensus_units == other.ai_units).all()
    assert [(f.id, f.units_p50) for f in s.execute(select(ForecastPoint)).scalars()] == before  # AI baseline immutable
    assert ov.override_revenue == pytest.approx(160 * ASP) and ov.ai_p50_units == 100.0 and ov.reason_code == "NEW_WIN"
    assert s.execute(select(AuditLog).where(AuditLog.action == "OVERRIDE_CREATE")).scalar_one()


def test_revenue_basis_converts_via_asp(fresh_db):
    s = fresh_db
    mini_run(s)
    ov = overrides.create_override(s, planner(), "run-1", "B", "Y", "ALL", MONTHS[1], "REVENUE", 30 * ASP, "PROJECT_DELAY", None)
    assert ov.override_units == pytest.approx(30.0)


def test_finest_level_pin_is_preserved_when_coarser_override_allocates(fresh_db):
    s = fresh_db
    mini_run(s)
    overrides.create_override(s, planner(), "run-1", "ALL", "X", "ALL", MONTHS[0], "UNITS", 300.0, "NEW_WIN", "x" * 20)  # region X: A(100)+B(80)=180 -> 300
    overrides.create_override(s, planner(), "run-1", "A", "X", "P", MONTHS[0], "UNITS", 100.0, "CAPACITY_CAP", None)  # pin A|X|P at 100
    b = cons(s).bottom
    m = b[b.month == MONTHS[0]].set_index(["oem", "region"])
    assert m.loc[("A", "X"), "consensus_units"] == pytest.approx(100.0)  # pinned
    assert m.loc[("B", "X"), "consensus_units"] == pytest.approx(200.0)  # residual 300-100 lands on the free leaf
    assert m.xs("X", level="region").consensus_units.sum() == pytest.approx(300.0)


def test_infeasible_combination_reported_as_conflict(fresh_db):
    s = fresh_db
    mini_run(s)
    overrides.create_override(s, planner(), "run-1", "A", "X", "P", MONTHS[0], "UNITS", 100.0, "NEW_WIN", None)
    overrides.create_override(s, planner(), "run-1", "ALL", "X", "ALL", MONTHS[0], "UNITS", 50.0, "NEW_WIN", "x" * 20)  # below pinned 100
    assert any("below the sum" in c["reason"] for c in cons(s).conflicts)


def test_revisions_supersede_and_keep_history(fresh_db):
    s = fresh_db
    mini_run(s)
    o1 = overrides.create_override(s, planner(), "run-1", "A", "X", "ALL", MONTHS[0], "UNITS", 110.0, "NEW_WIN", None)
    o2 = overrides.create_override(s, planner(), "run-1", "A", "X", "ALL", MONTHS[0], "UNITS", 120.0, "NEW_WIN", None)
    s.refresh(o1)
    assert (o1.status, o2.revision, o2.supersedes_id) == ("SUPERSEDED", 2, o1.id)
    assert cons(s).bottom.query("oem=='A' and region=='X' and month==@MONTHS[0]").consensus_units.sum() == pytest.approx(120.0)


def test_guardrails_scope_reason_and_lock(fresh_db):
    s = fresh_db
    mini_run(s)
    with pytest.raises(overrides.OverrideError, match="justification"):
        overrides.create_override(s, planner(), "run-1", "A", "X", "ALL", MONTHS[0], "UNITS", 400.0, "NEW_WIN", "short")
    overrides.create_override(s, planner(), "run-1", "A", "X", "ALL", MONTHS[0], "UNITS", 400.0, "NEW_WIN", "Customer signed LOI for volume ramp")
    with pytest.raises(overrides.OverrideError, match="reason_code"):
        overrides.create_override(s, planner(), "run-1", "A", "X", "ALL", MONTHS[1], "UNITS", 100.0, "BECAUSE", None)
    with pytest.raises(overrides.OverrideError, match="valid hierarchy"):
        overrides.create_override(s, planner(), "run-1", "A", "ALL", "P", MONTHS[1], "UNITS", 100.0, "NEW_WIN", None)
    with pytest.raises(overrides.ScopeError):
        overrides.create_override(s, rep(("X",)), "run-1", "A", "Y", "ALL", MONTHS[1], "UNITS", 60.0, "NEW_WIN", None)
    overrides.create_override(s, rep(("X",)), "run-1", "A", "X", "ALL", MONTHS[1], "UNITS", 110.0, "NEW_WIN", None)
    cycles.lock_run(s, "run-1", "planner@x")
    with pytest.raises(overrides.OverrideError, match="locked"):
        overrides.create_override(s, planner(), "run-1", "A", "X", "ALL", MONTHS[2], "UNITS", 100.0, "NEW_WIN", None)
    pts = s.execute(select(ConsensusPoint)).scalars().all()
    assert len(pts) == 12 and sum(p.overridden for p in pts) > 0 and all(p.naive_units >= 0 for p in pts)


def test_fva_math_matches_hand_calculation(fresh_db):
    """AI misses by 20%, consensus by 5%, naive by 40% on the overridden series -> FVA_sales = 0.20-0.05, FVA_AI = 0.40-0.20."""
    s = fresh_db
    mini_run(s)
    overrides.create_override(s, planner(), "run-1", "A", "X", "P", MONTHS[0], "UNITS", 115.0, "CUSTOMER_DIRECT_GUIDANCE", "Customer directed volume per weekly call")
    cycles.lock_run(s, "run-1", "p")
    actual = 120.0 / 1.0  # actual for A|X|P = 120 ; AI=100 (wMAPE 0.1667) consensus=115 (0.0417)
    for cp in s.execute(select(ConsensusPoint)).scalars():
        cp.naive_units = 60.0 if (cp.oem_code, cp.region_code, cp.month) == ("A", "X", MONTHS[0]) else cp.naive_units
    for (o, r), u in UNITS.items():
        for m in MONTHS:
            s.add(MappedSeries(month=m, oem_code=o, region_code=r, product_code="P", units=actual if (o, r, m) == ("A", "X", MONTHS[0]) else u, revenue_usd=u * ASP))
    s.flush()
    fva.compute_fva(s)
    row = s.execute(select(FvaResult).where(FvaResult.scope == "ALL", FvaResult.horizon.is_(None))).scalar_one()
    assert row.n_obs == 1
    assert row.wmape_ai == pytest.approx(20 / 120) and row.wmape_consensus == pytest.approx(5 / 120) and row.wmape_naive == pytest.approx(60 / 120)
    assert row.fva_sales == pytest.approx(15 / 120) and row.fva_ai == pytest.approx(40 / 120)
    allrows = s.execute(select(FvaResult).where(FvaResult.scope == "ALL_ROWS")).scalar_one()
    assert allrows.n_obs == 12 and allrows.fva_sales > 0  # un-overridden rows are identical for AI and consensus; the one override helps


def test_audit_chain_detects_tampering(fresh_db):
    s = fresh_db
    for i in range(4):
        audit(s, "u", "ACT", "thing", i, {"a": i}, {"a": i + 1})
    s.flush()
    assert verify_chain(s) == (True, None)
    row = s.execute(select(AuditLog).where(AuditLog.entity_id == "2")).scalar_one()
    row.after = {"a": 999}
    s.flush()
    ok, bad = verify_chain(s)
    assert not ok and bad == row.id
