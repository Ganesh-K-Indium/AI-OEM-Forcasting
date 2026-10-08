import numpy as np
import pytest

from app.ml import reconcile as R
from app.ml.hierarchy import build_hierarchy


def hier():
    b = [(o, r, p) for o in ("A", "B") for r in ("X", "Y") for p in ("p1", "p2")]
    return build_hierarchy(b)


def test_summing_matrix_structure():
    h = hier()
    S = h.S
    assert S.shape == (len(h.nodes), 8)
    assert S[0].sum() == 8  # total
    assert np.allclose(S[-8:], np.eye(8))  # bottom rows are identity
    assert set(h.tags) == {"TOTAL", "REGION", "OEM", "PRODUCT", "OEM_REGION", "BOTTOM"}
    idx = h.index()
    assert S[idx["A|ALL|ALL"]].sum() == 4 and S[idx["ALL|ALL|p1"]].sum() == 4  # grouped aggregates present


def test_P_properties():
    h = hier()
    rng = np.random.default_rng(0)
    resid = rng.normal(size=(len(h.nodes), 40))
    for m in R.METHODS:
        W = R.weight_matrix(h.S, resid, m)
        P = R.mint_P(h.S, W)
        assert np.allclose(P @ h.S, np.eye(8), atol=1e-8), m  # unbiasedness: P S = I


def test_coherence_and_idempotence():
    h = hier()
    rng = np.random.default_rng(1)
    resid = rng.normal(size=(len(h.nodes), 40))
    P = R.mint_P(h.S, R.weight_matrix(h.S, resid, "mint_shrink"))
    yhat = rng.uniform(5, 20, size=(len(h.nodes), 6))
    y = R.reconcile_point(h.S, P, yhat)
    assert R.coherence_error(h.S, y) < 1e-9
    assert np.allclose(R.reconcile_point(h.S, P, y), y)  # already-coherent forecasts are unchanged


def test_ols_matches_closed_form():
    h = hier()
    P = R.mint_P(h.S, np.eye(len(h.nodes)))
    ref = np.linalg.inv(h.S.T @ h.S) @ h.S.T
    assert np.allclose(P, ref)


def test_shrinkage_lambda_bounds_and_pd():
    rng = np.random.default_rng(2)
    W, lam = R.shrunk_covariance(rng.normal(size=(30, 12)))  # few observations -> strong shrinkage
    assert 0 <= lam <= 1 and np.all(np.linalg.eigvalsh(W) > 0)


def test_library_parity_ols_and_wls_var():
    from hierarchicalforecast.methods import MinTrace

    h = hier()
    rng = np.random.default_rng(3)
    y_in = rng.uniform(5, 30, size=(len(h.nodes), 36))
    fit = y_in + rng.normal(0, 1, y_in.shape)
    resid = y_in - fit
    for m in ("ols", "wls_var"):
        lib = MinTrace(method=m)
        lib.fit(S=h.S, y_hat=np.zeros((len(h.nodes), 1)), y_insample=y_in, y_hat_insample=fit, tags=h.tags)
        mine = R.mint_P(h.S, R.weight_matrix(h.S, resid, m))
        assert np.allclose(np.asarray(lib.P), mine, atol=1e-6), m


def test_library_engine_and_fallback_chain():
    h = hier()
    rng = np.random.default_rng(4)
    y_in = rng.uniform(5, 30, size=(len(h.nodes), 36))
    fit = y_in + rng.normal(0, 1, y_in.shape)
    res = R.fit_reconciler(h.S, y_in - fit, "mint_shrink", "hierarchicalforecast", y_in, fit, h.tags)
    assert res.method_used == "mint_shrink" and np.allclose(res.P @ h.S, np.eye(8), atol=1e-6)
    zero = np.zeros_like(y_in)  # degenerate (zero-inflated) residuals must not crash the run
    res2 = R.fit_reconciler(h.S, zero, "mint_shrink", "numpy")
    assert res2.method_used in ("wls_var", "ols", "mint_shrink") and np.all(np.isfinite(res2.P))


def test_sample_projection_is_coherent_and_nonnegative():
    h = hier()
    rng = np.random.default_rng(5)
    resid = rng.normal(size=(len(h.nodes), 40))
    P = R.mint_P(h.S, R.weight_matrix(h.S, resid, "mint_shrink"))
    mean = rng.uniform(1, 5, size=(len(h.nodes), 3))
    z = R.gaussian_copula_z(resid, 200, 3, rng)
    samp = R.two_piece_samples(mean, mean * 0.7, mean * 1.4, z)
    full, bottom = R.project_samples(h.S, P, samp)
    assert (bottom >= 0).all()
    assert np.allclose(full, np.einsum("nm,mhs->nhs", h.S, bottom))  # every sample path is coherent
    assert np.allclose(full[0], bottom.sum(axis=0))  # total = sum of bottoms
