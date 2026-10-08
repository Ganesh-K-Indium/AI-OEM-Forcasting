"""Time-series segmentation (Module 2): ADI / CV^2 (Syntetos-Boylan), STL seasonal strength, exogenous-driver score."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from statsmodels.tsa.seasonal import STL
from statsmodels.tsa.stattools import acf

SEGMENT_PRIORS: dict[str, list[str]] = {  # candidate models suggested per segment (a *prior*, champion is chosen by backtest)
    "smooth": ["AutoETS", "AutoARIMA"],
    "erratic": ["AutoETS", "LightGBM", "AutoARIMA"],
    "seasonal": ["SeasonalNaive", "LightGBM", "AutoETS"],
    "intermittent": ["CrostonSBA", "TSB"],
    "lumpy": ["TSB", "CrostonSBA"],
    "complex": ["Chronos-2", "TiRex-2", "LightGBM"],
}
SEGMENTS = list(SEGMENT_PRIORS)


@dataclass
class SegmentStats:
    n_obs: int
    adi: float
    cv2: float
    seasonality_strength: float
    acf12: float | None
    exog_strength: float
    segment: str


def adi_cv2(y: np.ndarray) -> tuple[float, float]:
    """ADI = periods / non-zero demands; CV^2 = (std/mean)^2 over non-zero demands."""
    y = np.asarray(y, dtype=float)
    nz = y[y > 0]
    if len(nz) == 0:
        return float("inf"), float("inf")
    adi = len(y) / len(nz)
    cv2 = (nz.std(ddof=0) / nz.mean()) ** 2 if len(nz) > 1 else 0.0
    return float(adi), float(cv2)


def seasonal_strength(y: np.ndarray, period: int = 12) -> float:
    """Wang-Smith-Hyndman STL seasonal strength Fs = max(0, 1 - Var(R)/Var(S+R)); needs >= 2 full periods."""
    y = np.asarray(y, dtype=float)
    if len(y) < 2 * period or np.allclose(y, y[0]):
        return 0.0
    try:
        res = STL(y, period=period, robust=True).fit()
    except Exception:
        return 0.0
    var_sr = np.var(res.seasonal + res.resid)
    return float(max(0.0, 1 - np.var(res.resid) / var_sr)) if var_sr > 0 else 0.0


def acf_lag(y: np.ndarray, lag: int = 12) -> float | None:
    y = np.asarray(y, dtype=float)
    if len(y) < lag + 6:
        return None
    return float(acf(y, nlags=lag, fft=True)[lag])


def exog_strength(y: np.ndarray, drivers: pd.DataFrame | None) -> float:
    """Max |corr| between first-differenced demand and (lagged) CRM/macro driver series; 0 when no drivers."""
    if drivers is None or drivers.empty or len(y) < 12:
        return 0.0
    dy = np.diff(np.asarray(y, dtype=float))
    best = 0.0
    for c in drivers.columns:
        x = drivers[c].to_numpy(dtype=float)[: len(y)]
        for lag in (0, 1, 2):
            xs = np.diff(x)
            a, b = (dy[lag:], xs[: len(xs) - lag]) if lag else (dy, xs)
            if len(a) > 6 and a.std() > 0 and b.std() > 0:
                best = max(best, abs(float(np.corrcoef(a, b)[0, 1])))
    return best


def classify(y: np.ndarray, drivers: pd.DataFrame | None, adi_cut: float = 1.32, cv2_cut: float = 0.49,
             seas_min: float = 0.70, acf_min: float = 0.25, exog_min: float = 0.55, min_seasonal_obs: int = 30) -> SegmentStats:
    y = np.asarray(y, dtype=float)
    adi, cv2 = adi_cv2(y)
    fs = seasonal_strength(y)
    a12 = acf_lag(y, 12)
    ex = exog_strength(y, drivers)
    if adi >= adi_cut:
        seg = "lumpy" if cv2 >= cv2_cut else "intermittent"
    elif ex >= exog_min:
        seg = "complex"
    elif len(y) >= min_seasonal_obs and fs >= seas_min and (a12 is None or a12 >= acf_min):  # STL Fs is biased upward on short noisy series -> corroborate with ACF(12)
        seg = "seasonal"
    else:
        seg = "erratic" if cv2 >= cv2_cut else "smooth"
    return SegmentStats(len(y), adi, cv2, fs, a12, ex, seg)
