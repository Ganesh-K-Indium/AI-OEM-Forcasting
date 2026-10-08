"""Global LightGBM forecaster: direct multi-horizon (horizon is a feature), scale-normalised target,
lag / rolling / calendar features, separate quantile models for P10/P90."""
from __future__ import annotations

import lightgbm as lgb
import numpy as np
import pandas as pd

from app.ml.base import DEFAULT_LEVELS, BaseForecastModel, ForecastOutput, future_index, monotone_quantiles, qcol

N_LAGS = 12


def base_features(y: np.ndarray, t: int) -> tuple[np.ndarray, float]:
    """Features from observations y[:t+1] only (no look-ahead). Returns (vector, scale)."""
    w = y[max(0, t - 11): t + 1]
    scale = float(w.mean())
    scale = scale if scale > 1e-9 else 1.0
    lags = np.array([y[t - k] / scale if t - k >= 0 else np.nan for k in range(N_LAGS)])
    r3, r6 = y[max(0, t - 2): t + 1].mean() / scale, y[max(0, t - 5): t + 1].mean() / scale
    sd6, sd12 = y[max(0, t - 5): t + 1].std() / scale, w.std() / scale
    zero_share = float((w <= 1e-9).mean())
    x = np.arange(len(w))
    slope = float(np.polyfit(x, w / scale, 1)[0]) if len(w) > 2 else 0.0
    mx, mn = w.max() / scale, w.min() / scale
    hist_ratio = scale / max(float(y[: t + 1].mean()), 1e-9)
    return np.concatenate([lags, [r3, r6, sd6, sd12, zero_share, slope, mx, mn, hist_ratio, np.log1p(scale)]]), scale


def horizon_features(y: np.ndarray, t: int, h: int, scale: float, month: int) -> np.ndarray:
    s1 = y[t + h - 12] / scale if t + h - 12 >= 0 and h <= 12 else np.nan
    s2 = y[t + h - 24] / scale if t + h - 24 >= 0 and h <= 24 else np.nan
    return np.array([h, month, np.sin(2 * np.pi * month / 12), np.cos(2 * np.pi * month / 12), float(month % 3 == 0), s1, s2])


class LightGBMDirectModel(BaseForecastModel):
    name, family = "LightGBM", "LightGBM"

    def __init__(self, horizon: int = 12, n_estimators: int = 220, learning_rate: float = 0.06, num_leaves: int = 15,
                 min_child_samples: int = 25, seed: int = 7, min_context: int = 12, quantiles=(0.1, 0.9)):
        self.H, self.min_context, self.seed = horizon, min_context, seed
        self.params = dict(n_estimators=n_estimators, learning_rate=learning_rate, num_leaves=num_leaves, min_child_samples=min_child_samples,
                           subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0, random_state=seed, verbose=-1, n_jobs=2)
        self.quantiles = quantiles
        self.models: dict[str, lgb.LGBMRegressor] = {}

    def fit(self, df, target_col, time_col, id_col="unique_id", **kw):
        d = df[[id_col, time_col, target_col]].rename(columns={id_col: "unique_id", time_col: "ds", target_col: "y"}).sort_values(["unique_id", "ds"])
        self.series_ = {u: g["y"].to_numpy(float) for u, g in d.groupby("unique_id")}
        self.last_ds_ = d["ds"].max()
        self.ds_index_ = {u: g["ds"].to_numpy() for u, g in d.groupby("unique_id")}
        X, Y = [], []
        for u, y in self.series_.items():
            ds = pd.DatetimeIndex(self.ds_index_[u])
            T = len(y)
            for t in range(self.min_context - 1, T - 1):
                bf, scale = base_features(y, t)
                for h in range(1, min(self.H, T - 1 - t) + 1):
                    month = ds[t + h].month if t + h < T else ds[t].month
                    X.append(np.concatenate([bf, horizon_features(y, t, h, scale, int(ds[t + h].month))]))
                    Y.append(y[t + h] / scale)
        X, Y = np.asarray(X), np.asarray(Y)
        if len(X) < 50:
            raise ValueError("insufficient history for LightGBM")
        self.train_rows_ = len(X)
        self.models["mean"] = lgb.LGBMRegressor(objective="regression", **self.params).fit(X, Y)
        for q in self.quantiles:
            self.models[qcol(q)] = lgb.LGBMRegressor(objective="quantile", alpha=q, **self.params).fit(X, Y)
        return self

    def predict(self, horizon, confidence_levels=None, **kw) -> ForecastOutput:
        levels = sorted(confidence_levels or DEFAULT_LEVELS)
        horizon = min(horizon, self.H)
        rows, meta = [], []
        fdates = future_index(pd.Timestamp(self.last_ds_), horizon)
        for u, y in self.series_.items():
            t = len(y) - 1
            bf, scale = base_features(y, t)
            for h in range(1, horizon + 1):
                rows.append(np.concatenate([bf, horizon_features(np.append(y, np.zeros(h)), t, h, scale, int(fdates[h - 1].month))]))
                meta.append((u, fdates[h - 1], h, scale))
        X = np.asarray(rows)
        m = pd.DataFrame(meta, columns=["unique_id", "ds", "h", "scale"])
        out = m[["unique_id", "ds", "h"]].copy()
        out["mean"] = np.clip(self.models["mean"].predict(X), 0, None) * m["scale"].to_numpy()
        for q in levels:
            if q == 0.5:
                out[qcol(q)] = out["mean"]
            elif qcol(q) in self.models:
                out[qcol(q)] = self.models[qcol(q)].predict(X) * m["scale"].to_numpy()
            else:
                out[qcol(q)] = out["mean"]
        out = monotone_quantiles(out, levels)
        for q in levels:
            if q < 0.5:
                out[qcol(q)] = np.minimum(out[qcol(q)], out["mean"])
            elif q > 0.5:
                out[qcol(q)] = np.maximum(out[qcol(q)], out["mean"])
        return ForecastOutput(out, self.name, levels, meta={"train_rows": self.train_rows_})

    def feature_importance(self) -> dict[str, float]:
        names = [f"lag{k}" for k in range(N_LAGS)] + ["r3", "r6", "sd6", "sd12", "zero_share", "slope", "max12", "min12", "hist_ratio", "log_scale",
                                                        "h", "month", "sin_m", "cos_m", "qtr_end", "seas_lag12", "seas_lag24"]
        imp = self.models["mean"].booster_.feature_importance("gain")
        return dict(sorted(zip(names, map(float, imp / max(imp.sum(), 1))), key=lambda kv: -kv[1]))
