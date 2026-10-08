"""Tier 3 - Minimum Trace (MinT) reconciliation.
   y_tilde = S (S' W^-1 S)^-1 S' W^-1 y_hat   with W = shrunk residual covariance (Schafer-Strimmer).
Engine: `hierarchicalforecast.MinTrace` (primary) with a numpy implementation used as parity check and fallback.
Probabilistic coherence: joint sample paths are projected with the same P matrix (Panagiotelis et al. 2023)."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

log = logging.getLogger(__name__)
METHODS = ("mint_shrink", "wls_var", "wls_struct", "ols")


# ----------------------------------------------------------------------------------- numpy MinT
def shrunk_covariance(resid: np.ndarray) -> tuple[np.ndarray, float]:
    """Schafer-Strimmer shrinkage of the sample covariance toward its diagonal. resid: (n_series, T). Returns (W, lambda)."""
    n, T = resid.shape
    X = resid - resid.mean(axis=1, keepdims=True)
    cov = (X @ X.T) / T
    sd = np.sqrt(np.clip(np.diag(cov), 1e-12, None))
    R = cov / np.outer(sd, sd)
    Xs = X / sd[:, None]
    # variance of the sample correlations (Opgen-Rhein & Strimmer)
    w = np.einsum("it,jt->ijt", Xs, Xs)
    wbar = w.mean(axis=2)
    var_r = (T / (T - 1) ** 3) * ((w - wbar[:, :, None]) ** 2).sum(axis=2)
    off = ~np.eye(n, dtype=bool)
    denom = (R[off] ** 2).sum()
    lam = float(np.clip(var_r[off].sum() / denom, 0.0, 1.0)) if denom > 0 else 1.0
    Rs = R * (1 - lam)
    np.fill_diagonal(Rs, 1.0)
    return Rs * np.outer(sd, sd), lam


def weight_matrix(S: np.ndarray, resid: np.ndarray | None, method: str) -> np.ndarray:
    n = S.shape[0]
    if method == "ols":
        return np.eye(n)
    if method == "wls_struct":
        return np.diag(S.sum(axis=1))
    if resid is None:
        raise ValueError(f"{method} needs in-sample residuals")
    if method == "wls_var":
        return np.diag(np.clip((resid ** 2).mean(axis=1), 1e-9, None))
    if method == "mint_shrink":
        return shrunk_covariance(resid)[0]
    raise ValueError(f"unknown method {method}")


def mint_P(S: np.ndarray, W: np.ndarray) -> np.ndarray:
    """P = (S' W^-1 S)^-1 S' W^-1  (maps n base forecasts -> m bottom-level reconciled forecasts)."""
    Wi_S = np.linalg.solve(W, S)  # W^-1 S
    A = S.T @ Wi_S
    return np.linalg.solve(A, Wi_S.T)


@dataclass
class ReconcileResult:
    P: np.ndarray
    method_used: str
    attempts: list[str]
    shrinkage_lambda: float | None = None


def fit_reconciler(S: np.ndarray, resid: np.ndarray | None, method: str = "mint_shrink", engine: str = "hierarchicalforecast",
                   y_insample: np.ndarray | None = None, y_hat_insample: np.ndarray | None = None, tags: dict | None = None) -> ReconcileResult:
    """Tries `method`, then wls_var, then ols (documented fallback for singular / zero-inflated covariance)."""
    chain = [method] + [m for m in ("wls_var", "ols") if m != method]
    attempts: list[str] = []
    for m in chain:
        try:
            P, lam = None, None
            if engine == "hierarchicalforecast" and y_insample is not None and y_hat_insample is not None:
                from hierarchicalforecast.methods import MinTrace

                rec = MinTrace(method=m)
                rec.fit(S=S, y_hat=np.zeros((S.shape[0], 1)), y_insample=y_insample, y_hat_insample=y_hat_insample, tags=tags)
                P = np.asarray(rec.P)
            else:
                W = weight_matrix(S, resid, m)
                P = mint_P(S, W)
                lam = shrunk_covariance(resid)[1] if m == "mint_shrink" else None
            if not np.all(np.isfinite(P)):
                raise FloatingPointError("non-finite projection matrix")
            attempts.append(f"{m}: ok")
            return ReconcileResult(P, m, attempts, lam)
        except Exception as e:  # noqa: BLE001
            attempts.append(f"{m}: {type(e).__name__}: {e}")
            log.warning("reconciliation method %s failed (%s) - falling back", m, e)
    raise RuntimeError("all reconciliation methods failed: " + "; ".join(attempts))


def reconcile_point(S: np.ndarray, P: np.ndarray, y_hat: np.ndarray, nonnegative: bool = True) -> np.ndarray:
    """y_hat: (n_nodes, H) -> coherent (n_nodes, H). Non-negativity: clip bottom level then re-aggregate (keeps coherence)."""
    b = P @ y_hat
    if nonnegative:
        b = np.clip(b, 0, None)
    return S @ b


def project_samples(S: np.ndarray, P: np.ndarray, samples: np.ndarray, nonnegative: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """samples: (n_nodes, H, N) incoherent joint sample paths -> (coherent node samples (n,H,N), bottom samples (m,H,N))."""
    n, H, N = samples.shape
    b = (P @ samples.reshape(n, H * N)).reshape(-1, H, N)
    if nonnegative:
        b = np.clip(b, 0, None)
    full = (S @ b.reshape(b.shape[0], H * N)).reshape(n, H, N)
    return full, b


def coherence_error(S: np.ndarray, y: np.ndarray) -> float:
    """max |y - S y_bottom| where bottom = last m rows (should be ~0 for coherent forecasts)."""
    m = S.shape[1]
    return float(np.abs(y - S @ y[-m:]).max())


def gaussian_copula_z(corr_resid: np.ndarray, n_samples: int, horizon: int, rng: np.random.Generator, ridge: float = 1e-3) -> np.ndarray:
    """Correlated standard-normal draws for each node & horizon step: (n_nodes, H, N). Correlation from (shrunk) residuals."""
    n = corr_resid.shape[0]
    W, _ = shrunk_covariance(corr_resid)
    sd = np.sqrt(np.clip(np.diag(W), 1e-12, None))
    C = W / np.outer(sd, sd)
    C = (1 - ridge) * C + ridge * np.eye(n)
    L = np.linalg.cholesky(C)
    z = rng.standard_normal((n, horizon * n_samples))
    return (L @ z).reshape(n, horizon, n_samples)


def two_piece_samples(mean: np.ndarray, q10: np.ndarray, q90: np.ndarray, z: np.ndarray) -> np.ndarray:
    """Marginal sample = mean + z * sigma_side (asymmetric normal fitted to the node's calibrated P10/P50/P90). Shapes (n,H) & (n,H,N)."""
    z10 = 1.2815515655446004
    s_lo = np.clip((mean - q10) / z10, 1e-9, None)[:, :, None]
    s_hi = np.clip((q90 - mean) / z10, 1e-9, None)[:, :, None]
    return np.clip(mean[:, :, None] + np.where(z < 0, z * s_lo, z * s_hi), 0, None)
