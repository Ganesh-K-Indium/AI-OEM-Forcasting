from datetime import date

import pandas as pd
import pytest
from sqlalchemy import select

from app.mapping import service
from app.mapping.fuzzy import name_similarity
from app.mapping.normalize import normalize_name
from app.models.reference import Account, AccountOemMapping, Oem


def test_normalize():
    assert normalize_name("Apple Inc.") == "apple"
    assert normalize_name("ROBERT BOSCH GMBH STUTTGART") == "robert bosch stuttgart"
    assert normalize_name("Dell'Orto SpA") == "dell orto"


def test_name_similarity_ranks_true_match_above_lookalike():
    g = {"operations", "sales", "international", "europe"}
    true = name_similarity(normalize_name("Apple Operations Europe Ltd"), "apple", g)
    fake = name_similarity(normalize_name("Applegate Industrial Supply LLC"), "apple", g)
    assert true > 0.9 and fake < 0.6


def _frame(rows):
    return pd.DataFrame(rows, columns=["month", "account_id", "end_account_id", "units"])


def _map(rows):
    return pd.DataFrame(rows, columns=["account_id", "oem_code", "region_code", "pct", "valid_from", "valid_to"]).assign(
        valid_from=lambda d: pd.to_datetime(d.valid_from), valid_to=lambda d: pd.to_datetime(d.valid_to))


def test_effective_dating_and_conservation():
    m = _map([(1, "A", "AMER", 1.0, "1900-01-01", "2025-04-01"), (1, "B", "AMER", 1.0, "2025-04-01", None)])
    f = _frame([(date(2025, 3, 1), 1, None, 10.0), (date(2025, 4, 1), 1, None, 20.0)])
    r = service.map_fact_frame(f, m, ["units"])
    assert r.set_index("month").oem_code.to_dict() == {pd.Timestamp("2025-03-01").date(): "A", pd.Timestamp("2025-04-01").date(): "B"} or list(r.oem_code) == ["A", "B"]
    assert r.units.sum() == pytest.approx(30.0)


def test_distributor_allocation_and_end_customer_precedence():
    m = _map([(10, "A", "EMEA", 0.7, "1900-01-01", None), (10, "B", "EMEA", 0.3, "1900-01-01", None), (20, "C", "EMEA", 1.0, "1900-01-01", None)])
    f = _frame([(date(2025, 1, 1), 10, None, 100.0), (date(2025, 1, 1), 10, 20, 50.0)])
    r = service.map_fact_frame(f, m, ["units"])
    by = r.groupby("oem_code").units.sum().to_dict()
    assert by == {"A": pytest.approx(70.0), "B": pytest.approx(30.0), "C": pytest.approx(50.0)}  # end-customer row goes wholly to C
    assert r.units.sum() == pytest.approx(150.0)


def test_unmapped_rows_are_kept():
    r = service.map_fact_frame(_frame([(date(2025, 1, 1), 99, None, 5.0)]), _map([(1, "A", "AMER", 1.0, "1900-01-01", None)]), ["units"])
    assert r.oem_code.tolist() == ["UNMAPPED"] and r.units.sum() == 5.0


def test_rule_engine_maps_identified_accounts(loaded_db, fresh_db=None):
    from app.core.db import SessionLocal

    with SessionLocal() as s:
        rows = s.execute(select(AccountOemMapping).where(AccountOemMapping.source.like("RULE_%"))).scalars().all()
        assert len(rows) >= 40 and all(m.status == "ACTIVE" and m.confidence >= 0.9 for m in rows)


def test_decoys_never_auto_activated(loaded_db, bundle):
    from app.core.db import SessionLocal

    decoys = set(bundle.meta["decoy_ids"])
    with SessionLocal() as s:
        active = s.execute(select(AccountOemMapping.account_id).where(AccountOemMapping.status == "ACTIVE")).scalars().all()
        assert not decoys & set(active)


def test_total_units_conserved_through_mapping(loaded_db, bundle):
    from app.core.db import SessionLocal
    from app.models.facts import MappedSeries
    from sqlalchemy import func

    with SessionLocal() as s:
        tot = s.execute(select(func.sum(MappedSeries.units))).scalar()
        assert tot == pytest.approx(bundle.sales.units.sum(), rel=1e-9)
        unm = s.execute(select(func.sum(MappedSeries.units)).where(MappedSeries.oem_code == "UNMAPPED")).scalar() or 0
        assert unm / tot < 0.05


def test_set_mapping_validations(fresh_db):
    from app.data.synthetic import REGIONS
    from app.models.reference import Region

    s = fresh_db
    for c, n in REGIONS.items():
        s.add(Region(code=c, name=n))
    s.add_all([Oem(id=1, code="A", name="A"), Oem(id=2, code="B", name="B")])
    s.add(Account(id=1, erp_customer_id="1", name="d", normalized_name="d", account_type="DISTRIBUTOR"))
    s.add(Account(id=2, erp_customer_id="2", name="x", normalized_name="x", account_type="DIRECT"))
    s.flush()
    with pytest.raises(service.MappingError, match="sum to 1.0"):
        service.set_mapping(s, 1, [dict(oem_code="A", region_code="AMER", allocation_pct=0.5)], None, "u")
    with pytest.raises(service.MappingError, match="only distributor"):
        service.set_mapping(s, 2, [dict(oem_code="A", region_code="AMER", allocation_pct=0.5), dict(oem_code="B", region_code="AMER", allocation_pct=0.5)], None, "u")
    service.set_mapping(s, 1, [dict(oem_code="A", region_code="AMER", allocation_pct=0.6), dict(oem_code="B", region_code="AMER", allocation_pct=0.4)], None, "u")
    # a later change closes the old rows at valid_from instead of deleting them
    service.set_mapping(s, 1, [dict(oem_code="B", region_code="AMER", allocation_pct=1.0)], date(2026, 1, 1), "u")
    old = s.execute(select(AccountOemMapping).where(AccountOemMapping.valid_to == date(2026, 1, 1))).scalars().all()
    assert len(old) == 2
