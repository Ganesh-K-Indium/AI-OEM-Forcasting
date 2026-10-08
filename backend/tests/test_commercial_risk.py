from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.ml import commercial as C
from app.risk.engine import Thr, _sev, threshold_for
from app.models.governance import RiskThreshold


def crm_fixture():
    months = pd.date_range("2025-01-01", periods=6, freq="MS")
    opps = pd.DataFrame(dict(id=[1, 2, 3], product_code=["P"] * 3, rep_id=[10, 10, 10], account_id=[1, 1, 1], end_account_id=[None] * 3,
                             created_month=[months[0]] * 3, ramp_months=[3, 3, 3], quantity_units=[100.0, 100.0, 100.0],
                             first_delivery_month=[pd.NaT] * 3))
    rows = []
    for oid, final in ((1, 6), (2, 7)):  # opp1 won in month 3, opp2 lost in month 3
        for i, m in enumerate(months[:4]):
            st = final if i == 3 else min(i + 1, 5)
            rows.append((oid, m, st, 100.0, months[min(i + 2, 5)], 0, 0, 0.5, True))
    for i, m in enumerate(months):  # opp3 stays open
        rows.append((3, m, 3, 100.0, months[min(i + 2, 5)], i, 0, 0.5, True))
    snaps = pd.DataFrame(rows, columns=["opportunity_id", "snapshot_month", "stage", "quantity_units", "expected_close_month", "months_in_stage", "push_count", "rep_probability", "quote_issued"])
    nm = pd.DataFrame(dict(opportunity_id=[1, 2, 3], oem_code=["A"] * 3, region_code=["X"] * 3, pct=[1.0] * 3))
    return C.CrmData(opps, snaps, nm, ["P"], ["X"]), months


def test_rep_stats_are_strictly_point_in_time():
    crm, months = crm_fixture()
    rs = C.rep_stats_by_month(crm).set_index("month")
    assert rs.loc[months[3], "rep_n_closed"] == 0  # both opps close in month index 3 -> not yet visible in that month
    assert rs.loc[months[4], "rep_n_closed"] == 2
    assert rs.loc[months[4], "rep_win_rate"] == pytest.approx((1 + 0.30 * 6) / (2 + 6))


def test_asof_never_sees_future_snapshots():
    crm, months = crm_fixture()
    assert crm.asof(months[2]).snaps.snapshot_month.max() == months[2]


def test_smoothed_distributions_are_probabilities():
    p = C._smooth_counts(np.array([0, 0, 1, 5, 99]), C.SLIP_SUPPORT)
    assert p.sum() == pytest.approx(1.0) and (p > 0).all()


def test_uplift_conserves_probability_mass():
    """With a long horizon every won deal is fully delivered: sum(expected units) == p_win * qty."""
    crm, months = crm_fixture()
    uni_s = np.full(len(C.SLIP_SUPPORT), 1 / len(C.SLIP_SUPPORT))
    uni_d = np.full(len(C.DELAY_SUPPORT), 1 / len(C.DELAY_SUPPORT))
    model = C.CommercialModel(None, None, {s: uni_s for s in range(1, 6)}, {"P": uni_d}, uni_d, {10: 1.0})
    asof = months[5]
    up = C.compute_uplift(crm, asof, model, ["A|X|P"], horizon=48, n_samples=0)
    expected_total = up.expected["A|X|P"].sum()
    assert expected_total == pytest.approx(C.STAGE_PRIOR[3] * 100.0, rel=1e-6)
    assert not up.detail.empty and (up.detail.expected_units >= 0).all()


def test_uplift_samples_mean_matches_expectation():
    crm, months = crm_fixture()
    uni_s = np.full(len(C.SLIP_SUPPORT), 1 / len(C.SLIP_SUPPORT))
    uni_d = np.full(len(C.DELAY_SUPPORT), 1 / len(C.DELAY_SUPPORT))
    model = C.CommercialModel(None, None, {s: uni_s for s in range(1, 6)}, {"P": uni_d}, uni_d, {10: 1.0})
    up = C.compute_uplift(crm, months[5], model, ["A|X|P"], horizon=24, n_samples=4000, seed=1)
    assert up.samples.sum(axis=(0, 1)).mean() == pytest.approx(up.expected.to_numpy().sum(), rel=0.08)


def test_threshold_precedence_and_severity():
    rows = [RiskThreshold(product_code=None, region_code=None, min_coverage=0.6), RiskThreshold(product_code="P", region_code=None, min_coverage=0.4),
            RiskThreshold(product_code="P", region_code="X", min_coverage=0.2)]
    assert threshold_for(rows, "P", "X").min_coverage == 0.2
    assert threshold_for(rows, "P", "Y").min_coverage == 0.4
    assert threshold_for(rows, "Q", "Y").min_coverage == 0.6
    assert (_sev(2e6), _sev(3e5), _sev(1e4)) == ("HIGH", "MEDIUM", "LOW")
