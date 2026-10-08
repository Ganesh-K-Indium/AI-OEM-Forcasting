"""Forecast accuracy metrics. Sign convention: bias = sum(actual - forecast) / sum(actual)  => positive = UNDER-forecast."""
from __future__ import annotations

import numpy as np


def _a(x) -> np.ndarray:
    return np.asarray(x, dtype=float)


def wmape(actual, forecast) -> float:
    a, f = _a(actual), _a(forecast)
    d = np.abs(a).sum()
    return float(np.abs(a - f).sum() / d) if d > 0 else float("nan")


def mae(actual, forecast) -> float:
    return float(np.abs(_a(actual) - _a(forecast)).mean())


def bias(actual, forecast) -> float:
    a, f = _a(actual), _a(forecast)
    d = a.sum()
    return float((a - f).sum() / d) if d != 0 else float("nan")


def mase_scale(train, season: int = 12) -> float:
    """In-sample MAE of the (seasonal) naive forecast; falls back to lag-1 when history is short."""
    y = _a(train)
    m = season if len(y) > season + 1 else 1
    d = np.abs(y[m:] - y[:-m])
    return float(d.mean()) if len(d) else float("nan")


def mase(actual, forecast, scale: float) -> float:
    if not np.isfinite(scale) or scale <= 0:
        return float("nan")
    return float(np.abs(_a(actual) - _a(forecast)).mean() / scale)


def pinball_loss(actual, q_forecast, q: float) -> float:
    a, f = _a(actual), _a(q_forecast)
    d = a - f
    return float(np.mean(np.maximum(q * d, (q - 1) * d)))


def mean_pinball(actual, quantiles: dict[float, np.ndarray]) -> float:
    return float(np.mean([pinball_loss(actual, v, q) for q, v in quantiles.items()]))


def interval_coverage(actual, lo, hi) -> float:
    a = _a(actual)
    return float(np.mean((a >= _a(lo)) & (a <= _a(hi))))
