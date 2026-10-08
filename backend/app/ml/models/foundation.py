"""Foundation-model plugins. Both degrade gracefully (ModelUnavailable) so the platform runs without GPUs / weights / licence sign-off."""
from __future__ import annotations

import importlib.util
from functools import lru_cache

import numpy as np
import pandas as pd

from app.core.config import get_settings
from app.ml.base import DEFAULT_LEVELS, BaseForecastModel, ForecastOutput, ModelUnavailable, future_index, monotone_quantiles, qcol


@lru_cache(maxsize=2)
def _chronos_pipeline(model_id: str):
    from chronos import Chronos2Pipeline

    return Chronos2Pipeline.from_pretrained(model_id, device_map="cpu")


class Chronos2Model(BaseForecastModel):
    """Amazon Chronos-2 (120M, Apache-2.0), zero-shot; supports past/future covariates (optional `covariates` frame)."""

    name, family = "Chronos-2", "Chronos-2"

    @classmethod
    def is_available(cls):
        s = get_settings()
        if not s.enable_chronos:
            return False, "disabled via ENABLE_CHRONOS=false"
        if importlib.util.find_spec("chronos") is None or importlib.util.find_spec("torch") is None:
            return False, "chronos-forecasting / torch not installed (pip install '.[foundation]')"
        return True, ""

    def fit(self, df, target_col, time_col, id_col="unique_id", covariates: list[str] | None = None, **kw):
        ok, why = self.is_available()
        if not ok:
            raise ModelUnavailable(why)
        cols = [id_col, time_col, target_col] + list(covariates or [])
        self.df_ = df[cols].rename(columns={id_col: "unique_id", time_col: "ds", target_col: "y"}).sort_values(["unique_id", "ds"]).reset_index(drop=True)
        self.covariates_ = list(covariates or [])
        return self

    def predict(self, horizon, confidence_levels=None, future_covariates: pd.DataFrame | None = None, **kw) -> ForecastOutput:
        levels = sorted(confidence_levels or DEFAULT_LEVELS)
        try:
            pipe = _chronos_pipeline(get_settings().chronos_model_id)
        except Exception as e:  # network / weights problems
            raise ModelUnavailable(f"could not load {get_settings().chronos_model_id}: {e}") from e
        qs = sorted(set(levels) | {0.5})
        pred = pipe.predict_df(self.df_, future_df=future_covariates, id_column="unique_id", timestamp_column="ds", target="y",
                               prediction_length=horizon, quantile_levels=qs, batch_size=128)
        pred = pred.rename(columns={"timestamp": "ds"}) if "timestamp" in pred.columns else pred
        out = pred[["unique_id", "ds"]].copy()
        out["h"] = out.groupby("unique_id").cumcount() + 1
        for q in qs:
            col = str(q) if str(q) in pred.columns else q
            out[qcol(q)] = pred[col].to_numpy()
        out["mean"] = pred["predictions"].to_numpy() if "predictions" in pred.columns else out[qcol(0.5)]
        out = out[["unique_id", "ds", "h", "mean"] + [qcol(q) for q in qs]]
        out = monotone_quantiles(out, qs)
        return ForecastOutput(out, self.name, qs)


class TiRexModel(BaseForecastModel):
    """NX-AI TiRex (xLSTM). Opt-in only: NXAI Community License (large-enterprise commercial use needs a licence)."""

    name, family = "TiRex-2", "TiRex-2"

    @classmethod
    def is_available(cls):
        s = get_settings()
        if not s.enable_tirex:
            return False, "disabled by default (NXAI Community License) - set ENABLE_TIREX=true after legal review"
        if importlib.util.find_spec("tirex") is None:
            return False, "tirex-ts not installed (pip install '.[tirex]')"
        return True, ""

    def fit(self, df, target_col, time_col, id_col="unique_id", **kw):
        ok, why = self.is_available()
        if not ok:
            raise ModelUnavailable(why)
        d = df[[id_col, time_col, target_col]].rename(columns={id_col: "unique_id", time_col: "ds", target_col: "y"}).sort_values(["unique_id", "ds"])
        self.series_ = {u: g["y"].to_numpy(np.float32) for u, g in d.groupby("unique_id")}
        self.last_ds_ = d["ds"].max()
        return self

    def predict(self, horizon, confidence_levels=None, **kw) -> ForecastOutput:  # pragma: no cover - needs tirex weights
        import torch
        from tirex import load_model

        levels = sorted(confidence_levels or DEFAULT_LEVELS)
        model = load_model(get_settings().tirex_model_id)
        ids = list(self.series_)
        ctx = [torch.tensor(self.series_[u]) for u in ids]
        qt, mean = model.forecast(context=ctx, prediction_length=horizon)  # qt: [B,h,9] levels 0.1..0.9
        grid = np.linspace(0.1, 0.9, 9)
        fd = future_index(pd.Timestamp(self.last_ds_), horizon)
        rows = []
        for i, u in enumerate(ids):
            for h in range(horizon):
                r = dict(unique_id=u, ds=fd[h], h=h + 1, mean=float(mean[i, h]))
                for q in levels:
                    r[qcol(q)] = float(np.interp(q, grid, qt[i, h].numpy()))
                rows.append(r)
        return ForecastOutput(monotone_quantiles(pd.DataFrame(rows), levels), self.name, levels)
