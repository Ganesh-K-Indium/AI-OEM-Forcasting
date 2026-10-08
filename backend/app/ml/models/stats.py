"""StatsForecast-backed models (ETS, ARIMA, SeasonalNaive, Croston-SBA, TSB, Naive) behind BaseForecastModel."""
from __future__ import annotations

import numpy as np
import pandas as pd
from statsforecast import StatsForecast
from statsforecast.models import ARIMA, AutoARIMA, AutoETS, CrostonSBA, Naive, SeasonalNaive, TSB

from app.ml.base import DEFAULT_LEVELS, BaseForecastModel, ForecastOutput, future_index, monotone_quantiles, qcol

Z = {0.5: 0.0}


def _central_levels(levels: list[float]) -> list[int]:
    return sorted({int(round(200 * abs(q - 0.5))) for q in levels if q != 0.5 and 0 < 200 * abs(q - 0.5) < 100})


class StatsModel(BaseForecastModel):
    family = "StatsForecast"
    sf_factory = None  # callable -> statsforecast model
    native_intervals = True
    min_obs = 4

    def __init__(self, season_length: int = 12):
        self.season_length = season_length
        self.df_: pd.DataFrame | None = None

    def fit(self, df, target_col, time_col, id_col="unique_id", **kw):
        d = df[[id_col, time_col, target_col]].rename(columns={id_col: "unique_id", time_col: "ds", target_col: "y"}).sort_values(["unique_id", "ds"])
        self.df_ = d.reset_index(drop=True)
        return self

    def _empirical(self, mean: pd.DataFrame, levels: list[float]) -> pd.DataFrame:
        """No native intervals (intermittent models): use the series' own historical marginal quantiles."""
        hist = self.df_.groupby("unique_id")["y"].apply(lambda s: s.to_numpy())
        out = mean.copy()
        for q in levels:
            out[qcol(q)] = out["unique_id"].map(lambda u: float(np.quantile(hist[u], q)))
        return out

    def predict(self, horizon, confidence_levels=None, **kw) -> ForecastOutput:
        levels = sorted(confidence_levels or DEFAULT_LEVELS)
        model = self.sf_factory()
        alias = model.alias if hasattr(model, "alias") else self.name
        sf = StatsForecast(models=[model], freq="MS", n_jobs=1)
        cl = _central_levels(levels)
        fc = None
        if self.native_intervals and cl:
            try:
                fc = sf.forecast(df=self.df_, h=horizon, level=cl)
            except Exception:
                fc = None
        if fc is None:
            fc = sf.forecast(df=self.df_, h=horizon)
            native = False
        else:
            native = True
        fc = fc.reset_index() if "unique_id" not in fc.columns else fc
        fc["h"] = fc.groupby("unique_id").cumcount() + 1
        out = fc[["unique_id", "ds", "h"]].copy()
        out["mean"] = np.clip(fc[alias].to_numpy(), 0, None)
        if native:
            for q in levels:
                if q == 0.5:
                    out[qcol(q)] = out["mean"]
                else:
                    lv = int(round(200 * abs(q - 0.5)))
                    col = f"{alias}-{'lo' if q < 0.5 else 'hi'}-{lv}"
                    out[qcol(q)] = fc[col].to_numpy() if col in fc else out["mean"]
        else:
            out = self._empirical(out, levels)
            if 0.5 in levels:
                out[qcol(0.5)] = out["mean"]
        out = monotone_quantiles(out, levels)
        for q in levels:  # keep the point forecast inside its own interval
            if q < 0.5:
                out[qcol(q)] = np.minimum(out[qcol(q)], out["mean"])
            elif q > 0.5:
                out[qcol(q)] = np.maximum(out[qcol(q)], out["mean"])
        return ForecastOutput(out, self.name, levels, meta={"native_intervals": native})

    def fitted_residuals(self) -> pd.DataFrame:
        """In-sample one-step residuals (used for MinT covariance estimation)."""
        sf = StatsForecast(models=[self.sf_factory()], freq="MS", n_jobs=1)
        sf.forecast(df=self.df_, h=1, fitted=True)
        fv = sf.forecast_fitted_values().reset_index() if "unique_id" not in sf.forecast_fitted_values().columns else sf.forecast_fitted_values()
        col = [c for c in fv.columns if c not in ("unique_id", "ds", "y")][0]
        fv["resid"] = fv["y"] - fv[col]
        return fv[["unique_id", "ds", "y", "resid"]]


def _mk(name, factory, native=True):
    return type(name.replace("-", ""), (StatsModel,), {"name": name, "sf_factory": lambda self, f=factory: f(self), "native_intervals": native})


NaiveModel = _mk("Naive", lambda s: Naive(), True)
SeasonalNaiveModel = _mk("SeasonalNaive", lambda s: SeasonalNaive(season_length=s.season_length), True)
AutoETSModel = _mk("AutoETS", lambda s: AutoETS(season_length=s.season_length), True)
AutoARIMAModel = _mk("AutoARIMA", lambda s: AutoARIMA(season_length=s.season_length, max_p=2, max_q=2, max_P=1, max_Q=1, max_d=1, max_D=1, stepwise=True), True)
CrostonSBAModel = _mk("CrostonSBA", lambda s: CrostonSBA(), False)
TSBModel = _mk("TSB", lambda s: TSB(alpha_d=0.2, alpha_p=0.2), False)
