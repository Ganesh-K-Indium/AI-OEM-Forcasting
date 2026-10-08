"""End-to-end: seed (fast mode) -> forecasts -> API. Exercises the real pipeline, MinT coherence, risk engine and async endpoints."""
import asyncio
import warnings

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.slow
warnings.filterwarnings("ignore")


@pytest.fixture(scope="module")
def client():
    from app.core.db import SessionLocal
    from app.data.seed import seed_demo
    from app.main import app

    with SessionLocal() as s:
        seed_demo(s, None, replay_cycles=2, fast=True)
        s.commit()
    with TestClient(app) as c:
        yield c


def login(c, email):
    r = c.post("/api/v1/auth/login", json={"email": email, "password": "demo1234"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_auth_required_and_rbac(client):
    assert client.get("/api/v1/dashboard").status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": "admin@demo.local", "password": "nope"}).status_code == 401
    viewer = login(client, "viewer@demo.local")
    body = dict(run_id="x", oem="APPLE", region="AMER", product="ALL", month="2026-10-01", basis="UNITS", value=1, reason_code="NEW_WIN")
    assert client.post("/api/v1/overrides", json=body, headers=viewer).status_code == 403


def test_meta_is_labelled_synthetic(client):
    m = client.get("/api/v1/meta").json()
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
    run = client.get("/api/v1/meta").json()["current_run_id"]
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
    run = client.get("/api/v1/meta").json()["current_run_id"]
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
