"""Unified forecasting-model interface (all tiers / libraries implement this)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

DEFAULT_LEVELS = [0.1, 0.5, 0.9]


def qcol(q: float) -> str:
    return f"q{int(round(q * 100)):02d}"


@dataclass
class ForecastOutput:
    """Long-format forecast. `frame` columns: unique_id, ds, h (1-based), mean, q10, q50, q90 ... (one q-column per level)."""

    frame: pd.DataFrame
    model_name: str
    levels: list[float] = field(default_factory=lambda: list(DEFAULT_LEVELS))
    meta: dict = field(default_factory=dict)

    def quantile(self, q: float) -> pd.DataFrame:
        return self.frame[["unique_id", "ds", "h", qcol(q)]].rename(columns={qcol(q): "yhat"})


class ModelUnavailable(RuntimeError):
    """Raised when an optional model (foundation model, licence-gated plugin) cannot run in this environment."""


class BaseForecastModel(ABC):
    name: str = "base"
    family: str = "Baseline"

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        return True, ""

    @abstractmethod
    def fit(self, df: pd.DataFrame, target_col: str, time_col: str, id_col: str = "unique_id", **kwargs) -> BaseForecastModel:
        """df: long format [id_col, time_col, target_col, *optional covariates]; months are month-start timestamps."""

    @abstractmethod
    def predict(self, horizon: int, confidence_levels: list[float] | None = None, **kwargs) -> ForecastOutput:
        """`confidence_levels` are *quantile levels* in (0,1), e.g. [0.1, 0.5, 0.9]."""


def monotone_quantiles(frame: pd.DataFrame, levels: list[float]) -> pd.DataFrame:
    """Enforce non-crossing, non-negative quantiles (demand cannot be negative)."""
    cols = [qcol(q) for q in sorted(levels)]
    arr = np.maximum.accumulate(np.clip(frame[cols].to_numpy(dtype=float), 0, None), axis=1)
    frame[cols] = arr
    return frame


def future_index(last_ds: pd.Timestamp, horizon: int) -> pd.DatetimeIndex:
    return pd.date_range(last_ds + pd.offsets.MonthBegin(1), periods=horizon, freq="MS")
