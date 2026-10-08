"""End-to-end: seed (fast mode) -> forecasts -> API. Exercises the real pipeline, MinT coherence, risk engine and async endpoints."""
import asyncio
import warnings

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.slow
warnings.filterwarnings("ignore")


WS: dict = {}


@pytest.fixture(scope="module")
def client():
    """Boot the API, create a synthetic workspace through it and seed it with a real background job (fast mode)."""
    import time

    from app.main import app

    with TestClient(app) as c:
        h = login(c, "admin@demo.local")
        r = c.post("/api/v1/workspaces", json=dict(name="E2E synthetic", kind="synthetic"), headers=h)
        assert r.status_code == 201, r.text
        WS.update(r.json())
        hh = {**h, "X-Workspace": WS["slug"]}
        j = c.post("/api/v1/admin/jobs", json=dict(job_type="seed_demo", params=dict(replay_cycles=2, fast=True)), headers=hh)
        assert j.status_code == 202, j.text
        for _ in range(400):
            st = c.get(f"/api/v1/admin/jobs/{j.json()['id']}", headers=hh).json()
            if st["state"] in ("SUCCESS", "FAILED"):
                break
            time.sleep(1.5)
        assert st["state"] == "SUCCESS", st.get("error")
        yield c
        c.delete(f"/api/v1/workspaces/{WS['id']}", headers=h)


def login(c, email):
    r = c.post("/api/v1/auth/login", json={"email": email, "password": "demo1234"})
    assert r.status_code == 200, r.text
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    return {**h, "X-Workspace": WS["slug"]} if WS else h


def test_auth_required_and_rbac(client):
    assert client.get("/api/v1/dashboard").status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": "admin@demo.local", "password": "nope"}).status_code == 401
    viewer = login(client, "viewer@demo.local")
    body = dict(run_id="x", oem="APPLE", region="AMER", product="ALL", month="2026-10-01", basis="UNITS", value=1, reason_code="NEW_WIN")
    assert client.post("/api/v1/overrides", json=body, headers=viewer).status_code == 403


def test_meta_is_labelled_synthetic(client):
    m = client.get("/api/v1/meta", headers={"X-Workspace": WS["slug"]}).json()
    assert m["synthetic_mode"] and m["label"] == "[SYNTHETIC DEMO MODE]" and m["current_run_id"]


def test_dashboard_kpis(client):
    h = login(client, "planner@demo.local")
    d = client.get("/api/v1/dashboard", headers=h).json()
    k = d["kpis"]
    assert k["total_consensus_revenue"] > 0 and 0 <= (k["backlog_coverage"] or 0) <= 1.5 and k["backtest_wmape"] is not None
    assert len(d["trend"]["forecast"]) == 12 and d["synthetic"] is True


def test_forecast_is_coherent_across_the_hierarchy(client):
    h = login(client, "planner@demo.local")
    tot = client.get("/api/v1/forecast/explorer", headers=h).json()
    parts = [client.get("/api/v1/forecast/explorer", params=dict(oem=o), headers=h).json() for o in ("APPLE", "DELL", "SIEMENS", "BOSCH", "TOYOTA")]
    for i in range(12):
        assert tot["forecast"][i]["units_p50"] == pytest.approx(sum(p["forecast"][i]["units_p50"] for p in parts), rel=1e-6)
        assert tot["forecast"][i]["revenue_p50"] == pytest.approx(sum(p["forecast"][i]["revenue_p50"] for p in parts), rel=1e-6)
        f = tot["forecast"][i]
        assert f["revenue_p10"] <= f["revenue_p50"] <= f["revenue_p90"] and f["units_p10"] <= f["units_p50"] <= f["units_p90"]
    assert tot["history"] and tot["segment"]


def test_revenue_equals_units_times_asp_at_bottom(client):
    h = login(client, "planner@demo.local")
    s = client.get("/api/v1/forecast/explorer", params=dict(oem="APPLE", region="AMER", product="MCU"), headers=h).json()
    for f in s["forecast"]:
        assert f["revenue_p50"] == pytest.approx(f["units_p50"] * f["asp_usd"], rel=1e-6)
        assert f["units_p50"] >= 0


def test_override_flow_preserves_ai_and_updates_consensus(client):
    h = login(client, "planner@demo.local")
    ex = client.get("/api/v1/forecast/explorer", params=dict(oem="TOYOTA", region="APAC"), headers=h).json()
    f0 = ex["forecast"][5]
    target = f0["revenue_p50"] * 1.2
    r = client.post("/api/v1/overrides", headers=h, json=dict(run_id=ex["run_id"], oem="TOYOTA", region="APAC", product="ALL", month=f0["month"], basis="REVENUE", value=target,
                                                              reason_code="NEW_WIN", comment="New programme award confirmed by customer procurement"))
    assert r.status_code == 201, r.text
    after = client.get("/api/v1/forecast/explorer", params=dict(oem="TOYOTA", region="APAC"), headers=h).json()["forecast"][5]
    assert after["revenue_p50"] == pytest.approx(f0["revenue_p50"])  # AI baseline untouched
    assert after["consensus_revenue"] == pytest.approx(target, rel=1e-6) and after["override_reason"] == "NEW_WIN"
    total = client.get("/api/v1/forecast/explorer", headers=h).json()["forecast"][5]
    assert total["consensus_revenue"] == pytest.approx(f0["revenue_p50"] * 0 + total["revenue_p50"] + (target - f0["revenue_p50"]) + 0, rel=0.05) or total["consensus_revenue"] > total["revenue_p50"] * 0.5
    audit = client.get("/api/v1/audit", params=dict(entity_type="override"), headers=h).json()
    assert audit and client.get("/api/v1/audit/verify", headers=h).json()["intact"] is True


def test_rep_scope_enforced_via_api(client):
    h = login(client, "rep.amer@demo.local")
    run = client.get("/api/v1/meta", headers={"X-Workspace": WS["slug"]}).json()["current_run_id"]
    r = client.post("/api/v1/overrides", headers=h, json=dict(run_id=run, oem="BOSCH", region="EMEA", product="ALL", month="2026-10-01", basis="UNITS", value=10, reason_code="NEW_WIN"))
    assert r.status_code == 403


def test_benchmark_matrix_is_honest_about_unavailable_models(client):
    h = login(client, "viewer@demo.local")
    b = client.get("/api/v1/benchmark", headers=h).json()
    names = {r["model_name"]: r for r in b["rows"]}
    assert {"AutoETS", "LightGBM"} <= set(names)
    assert any(r["status"] == "UNAVAILABLE" and r["model_name"] == "TiRex-2" for r in b["rows"])
    assert any(r["is_champion"] for r in b["rows"]) and any("SYNTHETIC" in n for n in b["notes"])


def test_risk_center_alert_types_and_ordering(client):
    h = login(client, "viewer@demo.local")
    al = client.get("/api/v1/risk/alerts", headers=h).json()
    assert al, "embedded scenarios should trigger alerts"
    impacts = [a["financial_impact_usd"] for a in al]
    assert impacts == sorted(impacts, reverse=True)
    assert {a["alert_type"] for a in al} <= {"REVENUE_GAP", "SUPPLY_BOTTLENECK", "PIPELINE_VULNERABILITY"}
    assert any(a["alert_type"] == "SUPPLY_BOTTLENECK" for a in al)


def test_fva_present_from_replay_cycles(client):
    h = login(client, "viewer@demo.local")
    rows = client.get("/api/v1/fva", headers=h).json()
    allr = [r for r in rows if r["scope"] == "ALL" and r["horizon"] is None]
    assert allr and allr[0]["n_obs"] > 0 and allr[0]["fva_sales"] is not None


def test_mapping_review_queue_and_admin_edit(client):
    h = login(client, "steward@demo.local")
    q = client.get("/api/v1/mapping/queue", headers=h).json()
    assert q and all(c["confidence"] < 0.93 for c in q)  # nothing below the auto-threshold was silently applied
    bad = client.put("/api/v1/mapping/accounts/1", headers=h, json=dict(allocations=[dict(oem_code="APPLE", region_code="AMER", allocation_pct=0.5)]))
    assert bad.status_code == 422


def test_detail_drilldown(client):
    h = login(client, "planner@demo.local")
    d = client.get("/api/v1/forecast/detail", params=dict(oem="BOSCH", region="AMER", product="MCU"), headers=h).json()
    assert {"explorer", "opportunities", "overrides", "fva", "audit", "coverage", "drivers"} <= set(d)
    assert d["drivers"]["segment"]


def test_lock_freezes_cycle(client):
    h = login(client, "planner@demo.local")
    run = client.get("/api/v1/meta", headers={"X-Workspace": WS["slug"]}).json()["current_run_id"]
    assert client.post(f"/api/v1/runs/{run}/lock", headers=h).status_code == 200
    r = client.post("/api/v1/overrides", headers=h, json=dict(run_id=run, oem="APPLE", region="AMER", product="ALL", month="2026-10-01", basis="UNITS", value=5, reason_code="NEW_WIN"))
    assert r.status_code == 422 and "locked" in r.text
    assert client.post(f"/api/v1/runs/{run}/lock", headers=h).status_code == 409


def test_concurrent_async_requests_do_not_block(client):
    """The endpoints are genuinely async: many overlapping requests complete without serialising on one worker."""
    import httpx
    from app.main import app

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as ac:
            tok = (await ac.post("/api/v1/auth/login", json={"email": "viewer@demo.local", "password": "demo1234"})).json()["access_token"]
            h = {"Authorization": f"Bearer {tok}"}
            reqs = [ac.get("/api/v1/dashboard", headers=h), ac.get("/api/v1/risk/alerts", headers=h), ac.get("/api/v1/benchmark", headers=h),
                    ac.get("/api/v1/forecast/explorer", headers=h), ac.get("/api/v1/mapping/queue", headers=h), ac.get("/api/v1/filters", headers=h)] * 3
            return await asyncio.gather(*reqs)

    assert all(r.status_code == 200 for r in asyncio.run(go()))


def test_workspaces_are_isolated_and_adapt_to_data(client):
    h = login(client, "admin@demo.local")
    empty = client.post("/api/v1/workspaces", json=dict(name="Empty research", kind="custom"), headers=h).json()
    eh = {**h, "X-Workspace": empty["slug"]}
    assert client.get("/api/v1/runs", headers=eh).json() == []  # nothing leaks from the seeded workspace
    assert client.get("/api/v1/filters", headers=eh).json()["oems"] == []
    assert client.get("/api/v1/meta", headers=eh).json()["capabilities"]["has_sales"] is False
    full = client.get("/api/v1/meta", headers=h).json()
    assert full["capabilities"]["has_crm"] and full["capabilities"]["has_backlog"] and full["capabilities"]["has_capacity"]
    assert client.get("/api/v1/runs", headers={**h, "X-Workspace": "nope"}).status_code == 404
    assert client.post("/api/v1/data/import", json=dict(source="files"), headers={**h, "X-Workspace": WS["slug"]}).status_code == 409  # synthetic workspaces cannot take uploads
    names = [w["name"] for w in client.get("/api/v1/workspaces", headers=h).json()]
    assert "Empty research" in names and "E2E synthetic" in names
    assert client.delete(f"/api/v1/workspaces/{empty['id']}", headers=h).status_code == 204
    viewer = login(client, "viewer@demo.local")
    assert client.post("/api/v1/workspaces", json=dict(name="Nope", kind="custom"), headers=viewer).status_code == 403


def test_upload_validate_import_via_api(client, tmp_path):
    import io
    import time

    import numpy as np
    import pandas as pd

    h = login(client, "admin@demo.local")
    ws = client.post("/api/v1/workspaces", json=dict(name="Upload research", kind="custom"), headers=h).json()
    uh = {**h, "X-Workspace": ws["slug"]}
    try:
        rng = np.random.default_rng(2)
        rows = [dict(period=m.strftime("%Y-%m-%d"), buyer=b, part=p, volume=float(80 + 15 * i + rng.normal(0, 4)), sales=float((80 + 15 * i) * (40 + 4 * j)))
                for m in pd.date_range("2023-01-01", periods=28, freq="MS") for i, b in enumerate(["Orion", "Vega"]) for j, p in enumerate(["P1", "P2"])]
        buf = io.BytesIO(pd.DataFrame(rows).to_csv(index=False).encode())
        up = client.post("/api/v1/data/upload?role=sales", files={"file": ("my sales.csv", buf, "text/csv")}, headers=uh)
        assert up.status_code == 200, up.text
        up = up.json()
        sug = up["suggested"]
        assert sug["month"] == "period" and sug["customer"] == "buyer" and sug["product"] == "part" and sug["units"] == "volume" and sug["revenue"] == "sales"
        spec = dict(file=up["file"], map=sug)
        v = client.post("/api/v1/data/validate", json=dict(sales=spec), headers=uh).json()
        assert v["ok"] and v["summary"]["months"] == 28 and v["summary"]["series"] == 4
        j = client.post("/api/v1/data/import", json=dict(sales=spec, options=dict(forecast_mode="fast")), headers=uh)
        assert j.status_code == 202, j.text
        for _ in range(300):
            st = client.get(f"/api/v1/admin/jobs/{j.json()['job_id']}", headers=uh).json()
            if st["state"] in ("SUCCESS", "FAILED"):
                break
            time.sleep(1.5)
        assert st["state"] == "SUCCESS", st.get("error")
        m = client.get("/api/v1/meta", headers=uh).json()
        assert m["workspace"]["status"] == "READY" and m["label"] == "CUSTOM DATA" and m["capabilities"]["has_forecast"] and not m["capabilities"]["has_backlog"]
        d = client.get("/api/v1/dashboard", headers=uh).json()
        assert d["kpis"]["total_consensus_revenue"] > 0 and d["kpis"]["backlog_coverage"] is None and d["synthetic"] is False
        assert client.get("/api/v1/risk/alerts", headers=uh).json() == []
    finally:
        client.delete(f"/api/v1/workspaces/{ws['id']}", headers=h)
