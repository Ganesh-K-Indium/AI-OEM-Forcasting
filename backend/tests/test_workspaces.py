"""Workspace isolation (schema per workspace), pgvector search, concurrent audit chain, import wizard + M5 preset on Postgres."""
import threading
from datetime import date

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select, text

from app.core import workspace as wsmod
from app.core.db import SessionLocal, current_schema, engine, use_workspace
from app.models.ops import Workspace


@pytest.fixture()
def two_workspaces():
    with SessionLocal(None) as s:
        a = wsmod.create_workspace(s, "Alpha research", "custom", None, "test")
        b = wsmod.create_workspace(s, "Beta research", "custom", None, "test")
        ids = (a.schema_name, b.schema_name, a.id, b.id)
    yield ids
    with SessionLocal(None) as s:
        for wid in ids[2:]:
            try:
                wsmod.delete_workspace(s, wid)
            except KeyError:
                pass  # already deleted by the test


def test_schema_per_workspace_isolates_data(two_workspaces):
    from app.models.reference import Region

    sa, sb, *_ = two_workspaces
    with use_workspace(sa), SessionLocal() as s:
        s.add(Region(code="EU", name="Europe"))
        s.commit()
    with use_workspace(sa), SessionLocal() as s:
        assert [r.code for r in s.execute(select(Region)).scalars()] == ["EU"]
    with use_workspace(sb), SessionLocal() as s:
        assert s.execute(select(Region)).scalars().all() == []  # another workspace never sees it
    with engine.connect() as c:
        schemas = {r[0] for r in c.execute(text("select table_schema from information_schema.tables where table_name='regions'"))}
    assert {sa, sb} <= schemas and "public" not in schemas  # workspace tables never live in public


def test_shared_tables_are_visible_from_every_workspace(two_workspaces):
    from app.models.ops import User

    sa, *_ = two_workspaces
    with use_workspace(sa), SessionLocal() as s:
        s.add(User(email="shared@x.com", full_name="S", role="viewer"))
        s.commit()
    with SessionLocal(None) as s:
        assert s.execute(select(User).where(User.email == "shared@x.com")).scalar_one()
        s.query(User).filter(User.email == "shared@x.com").delete()
        s.commit()


def test_delete_workspace_drops_schema(two_workspaces):
    sa, _, wid, _ = two_workspaces
    with SessionLocal(None) as s:
        wsmod.delete_workspace(s, wid)
    with engine.connect() as c:
        assert c.execute(text("select count(*) from information_schema.schemata where schema_name=:n"), {"n": sa}).scalar() == 0
        assert wsmod.check_schema(sa)


def test_schema_name_is_validated():
    with pytest.raises(ValueError):
        wsmod.check_schema('ws_x"; drop schema public; --')


def test_pgvector_nearest_alias_uses_cosine_operator(fresh_db):
    from app.mapping.embeddings import get_embedder
    from app.mapping.vector_store import nearest_aliases
    from app.models.reference import Oem, OemAlias

    s = fresh_db
    emb = get_embedder()
    for i, (code, alias) in enumerate([("APPLE", "apple inc"), ("DELL", "dell technologies"), ("BOSCH", "robert bosch gmbh")], 1):
        s.add(Oem(id=i, code=code, name=code))
        s.flush()
        s.add(OemAlias(oem_id=i, alias=alias, normalized_alias=alias, embedding=emb.embed([alias])[0]))
    s.commit()
    hits = nearest_aliases(s, emb.embed(["apple incorporated"])[0], k=3)
    assert hits[0][1] == 1 and hits[0][2] > hits[-1][2]


def test_audit_hash_chain_stays_intact_under_concurrent_writers(fresh_db):
    from app.core.audit import audit, verify_chain
    from app.models.governance import AuditLog

    schema = current_schema.get()

    def worker(w):
        with use_workspace(schema):
            for i in range(15):
                with SessionLocal() as s:
                    audit(s, f"u{w}", "ACT", "thing", f"{w}-{i}", None, {"i": i})
                    s.commit()

    ts = [threading.Thread(target=worker, args=(w,)) for w in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    with SessionLocal() as s:
        assert len(s.execute(select(AuditLog)).scalars().all()) == 90
        assert verify_chain(s) == (True, None)


# ------------------------------------------------------------------------------------------------ importer
def _sales_csv(path, months=30, customers=("Acme", "Globex", "Initech"), products=("W1", "W2"), regions=None):
    rng = np.random.default_rng(1)
    rows = []
    for m in pd.date_range("2023-01-01", periods=months, freq="MS"):
        for ci, c in enumerate(customers):
            for p in products:
                u = 100 + 10 * ci + 20 * np.sin(m.month / 12 * 6.28) + rng.normal(0, 3)
                rows.append(dict(Date=m.date().isoformat(), Client=c, SKU=p, Qty=round(u, 1), Amount=round(u * (50 + 5 * ci), 2), Territory=(regions or ["EMEA", "AMER", "APAC"])[ci % 3]))
    pd.DataFrame(rows).to_csv(path, index=False)


def test_import_suggests_columns_and_validates(tmp_path):
    from app.data import importer

    f = tmp_path / "s.csv"
    _sales_csv(f)
    cols = list(pd.read_csv(f, nrows=1).columns)
    sug = importer.suggest_mapping("sales", cols)
    assert sug["month"] == "Date" and sug["customer"] == "Client" and sug["product"] == "SKU" and sug["units"] == "Qty" and sug["revenue"] == "Amount" and sug["region"] == "Territory"
    rep = importer.validate_sales(f, sug)
    assert rep["ok"] and rep["summary"]["months"] == 30 and rep["summary"]["series"] == 6
    short = tmp_path / "short.csv"
    _sales_csv(short, months=10)
    bad = importer.validate_sales(short, sug)
    assert not bad["ok"] and any("18" in i["message"] for i in bad["issues"])
    no_rev = {**sug, "revenue": None}
    assert not importer.validate_sales(f, no_rev)["ok"]
    assert importer.validate_sales(f, no_rev, {"constant_price": 10})["ok"]


def test_custom_import_without_crm_backlog_capacity_forecasts_end_to_end(tmp_path):
    """The whole point of capability flags: sales history only -> forecast works; CRM/backlog/capacity features are cleanly off."""
    from app.data import importer
    from app.models.forecast import ForecastPoint, ForecastRun
    from app.models.governance import RiskAlert

    f = tmp_path / "s.csv"
    _sales_csv(f)
    cols = list(pd.read_csv(f, nrows=1).columns)
    with SessionLocal(None) as s:
        w = wsmod.create_workspace(s, "Import test", "custom", None, "test")
        sch, wid = w.schema_name, w.id
    try:
        base = importer.upload_dir(wid)
        (base / "s.csv").write_bytes(f.read_bytes())
        params = dict(source="files", workspace_id=wid, sales=dict(file="s.csv", map=importer.suggest_mapping("sales", cols)), options=dict(forecast_mode="fast"))
        with use_workspace(sch), SessionLocal() as s:
            out = importer.run_import(s, params, "tester", lambda f, m: None)
            s.commit()
        assert out["loaded"]["materialized"]["unmapped_share"] == 0
        caps = wsmod.refresh_capabilities(sch, wid)
        assert caps["has_sales"] and caps["has_mapped"] and caps["has_forecast"] and not caps["has_crm"] and not caps["has_backlog"] and not caps["has_capacity"]
        assert caps["oems"] == 3 and caps["regions"] == 3 and caps["products"] == 2
        with use_workspace(sch), SessionLocal() as s:
            run = s.execute(select(ForecastRun)).scalar_one()
            assert run.status == "COMPLETED" and not run.is_synthetic
            assert s.execute(select(ForecastPoint).where(ForecastPoint.run_id == run.id, ForecastPoint.level == "TOTAL")).scalars().all()
            assert s.execute(select(RiskAlert)).scalars().all() == []  # no backlog / capacity / CRM -> no (false) risk alerts
    finally:
        with SessionLocal(None) as s:
            wsmod.delete_workspace(s, wid)


def _write_m5(d, n_days=900):
    """Tiny file set in the real M5 layout (2 stores in 2 states, 2 departments)."""
    d.mkdir(parents=True, exist_ok=True)
    days = pd.date_range("2013-01-01", periods=n_days)
    cal = pd.DataFrame({"d": [f"d_{i + 1}" for i in range(n_days)], "date": days.strftime("%Y-%m-%d"), "wm_yr_wk": [11100 + i // 7 for i in range(n_days)]})
    cal.to_csv(d / "calendar.csv", index=False)
    rng = np.random.default_rng(3)
    items = [("FOODS_1_001", "FOODS_1", "FOODS"), ("FOODS_1_002", "FOODS_1", "FOODS"), ("HOBBIES_1_001", "HOBBIES_1", "HOBBIES")]
    stores = [("CA_1", "CA"), ("TX_1", "TX")]
    rows, prices = [], []
    for it, dept, cat in items:
        for st, state in stores:
            u = rng.poisson(8, n_days)
            rows.append({"id": f"{it}_{st}_evaluation", "item_id": it, "dept_id": dept, "cat_id": cat, "store_id": st, "state_id": state, **{f"d_{i + 1}": int(u[i]) for i in range(n_days)}})
            for wk in sorted(cal.wm_yr_wk.unique()):
                prices.append(dict(store_id=st, item_id=it, wm_yr_wk=int(wk), sell_price=2.5))
    pd.DataFrame(rows).to_csv(d / "sales_train_evaluation.csv", index=False)
    pd.DataFrame(prices).to_csv(d / "sell_prices.csv", index=False)


def test_m5_preset_maps_store_state_department(tmp_path):
    from app.data.m5 import load_m5, m5_available

    d = tmp_path / "m5"
    assert not m5_available(d)["available"]
    _write_m5(d)
    assert m5_available(d)["available"]
    c, issues, extra = load_m5(d, {}, lambda f, m: None)
    assert set(c.oem) == {"CA_1", "TX_1"} and set(c.region) == {"California", "Texas"} and set(c["product"]) == {"FOODS_1", "HOBBIES_1"}
    assert any("incomplete last month" in i["message"] for i in issues)  # 900 days from 2013-01-01 ends mid-June 2015
    assert c.revenue.sum() == pytest.approx(c.units.sum() * 2.5)
    assert c.month.max() < pd.Timestamp("2015-06-01")


def test_zip_unpack_ssrf_guard_and_m5_zip(tmp_path):
    import zipfile

    from fastapi import HTTPException

    from app.api import data as api

    (tmp_path / "a.csv").write_text("month,customer\n2024-01-01,x\n")
    z = tmp_path / "up.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.write(tmp_path / "a.csv", "nested/dir/a.csv")
        zf.writestr("readme.txt.bak", "ignore")
    p, name = api._unpack(z, tmp_path, "abcd")
    assert name == "a.csv" and p.read_text().startswith("month") and p.parent == tmp_path  # extracted flat, no traversal
    empty = tmp_path / "e.zip"
    with zipfile.ZipFile(empty, "w") as zf:
        zf.writestr("x.pdf", "no data")
    with pytest.raises(HTTPException):
        api._unpack(empty, tmp_path, "abcd")
    for bad in ("http://127.0.0.1/x.csv", "http://localhost:8000/a", "http://169.254.169.254/latest", "file:///etc/passwd", "ftp://example.com/a.csv"):
        with pytest.raises(HTTPException):
            api._check_public_url(bad)
    m5 = tmp_path / "m5.zip"
    with zipfile.ZipFile(m5, "w") as zf:
        for n in ("calendar.csv", "sell_prices.csv", "sales_train_evaluation.csv", "sample_submission.csv"):
            zf.writestr(f"deep/{n}", "x")
    out = tmp_path / "out"
    out.mkdir()
    assert sorted(api._store_m5(m5, out, "m5.zip")) == ["calendar.csv", "sales_train_evaluation.csv", "sell_prices.csv"]
    assert not (out / "sample_submission.csv").exists()
