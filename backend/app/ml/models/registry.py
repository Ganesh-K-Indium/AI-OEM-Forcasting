from __future__ import annotations

from collections.abc import Callable

from app.ml.base import BaseForecastModel
from app.ml.models.foundation import Chronos2Model, TiRexModel
from app.ml.models.lgbm import LightGBMDirectModel
from app.ml.models.stats import AutoARIMAModel, AutoETSModel, CrostonSBAModel, NaiveModel, SeasonalNaiveModel, TSBModel

ModelFactory = Callable[[], BaseForecastModel]


def model_catalog(horizon: int = 12) -> dict[str, tuple[ModelFactory, type[BaseForecastModel]]]:
    """name -> (factory, class). Order = execution order."""
    return {
        "Naive": (lambda: NaiveModel(), NaiveModel),
        "SeasonalNaive": (lambda: SeasonalNaiveModel(), SeasonalNaiveModel),
        "AutoETS": (lambda: AutoETSModel(), AutoETSModel),
        "AutoARIMA": (lambda: AutoARIMAModel(), AutoARIMAModel),
        "CrostonSBA": (lambda: CrostonSBAModel(), CrostonSBAModel),
        "TSB": (lambda: TSBModel(), TSBModel),
        "LightGBM": (lambda: LightGBMDirectModel(horizon=horizon), LightGBMDirectModel),
        "Chronos-2": (lambda: Chronos2Model(), Chronos2Model),
        "TiRex-2": (lambda: TiRexModel(), TiRexModel),
    }
