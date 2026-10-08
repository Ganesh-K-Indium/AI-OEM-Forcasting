import numpy as np

from app.data.synthetic import generate


def test_deterministic():
    a, b = generate(7, n_opps=300), generate(7, n_opps=300)
    assert a.sales.equals(b.sales) and a.opportunities.equals(b.opportunities) and a.scenarios == b.scenarios
    c = generate(8, n_opps=300)
    assert not a.sales.units.equals(c.sales.units)


def test_dimensions(bundle):
    assert len(bundle.oems) == 5 and set(bundle.oems.code) == {"APPLE", "DELL", "SIEMENS", "BOSCH", "TOYOTA"}
    assert len(bundle.products) == 8
    assert bundle.sales.month.nunique() == 36
    assert len(bundle.truth.series.unique()) == 120
    assert {"AMER", "EMEA", "APAC"} == set(bundle.capacity.region_code)


def test_scenarios_embedded(bundle):
    tags = set(bundle.scenarios.values())
    assert {"supply_bottleneck", "pipeline_push", "trend_break", "intermittent", "quarter_end_spike", "steady_growth", "under_coverage"} <= tags


def test_opportunity_volume_and_snapshots(bundle):
    assert len(bundle.opportunities) >= 1000  # test config; the default config yields 2,000+
    assert {1, 2, 3, 4, 5, 6, 7} <= set(bundle.snapshots.stage)
    last = bundle.snapshots.snapshot_month.max()
    assert (bundle.snapshots.snapshot_month <= last).all()
    # point-in-time: an opp has at most one snapshot per month
    assert not bundle.snapshots.duplicated(["opportunity_id", "snapshot_month"]).any()


def test_default_config_has_2000_plus_opps():
    assert len(generate().opportunities) >= 2000


def test_distributor_allocations_sum_to_one(bundle):
    s = bundle.manual_mappings.groupby("account_id").allocation_pct.sum()
    assert np.allclose(s, 1.0)


def test_capacity_bottleneck_present(bundle):
    t = bundle.truth
    t = t[t.series.str.endswith("|APAC|MEM")]
    assert (t.demand - t.units > 1e-6).any()  # shipments capped below demand
