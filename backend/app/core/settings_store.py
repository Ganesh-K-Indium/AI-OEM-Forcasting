"""Admin-editable settings persisted in the DB (thresholds, fuzzy cut-offs, FX policy ...)."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.reference import SystemSetting

DEFAULTS: dict[str, tuple[Any, str]] = {
    "fx_policy": ("constant", "constant = convert all months at fx_base_month rates; actual = month rates"),
    "fx_base_month": (None, "ISO month for constant-currency conversion (null = latest actual month)"),
    "fuzzy_auto_threshold": (0.93, "score >= this auto-activates a fuzzy mapping"),
    "fuzzy_review_threshold": (0.62, "score >= this (and < auto) is queued for steward review"),
    "fuzzy_weights": ({"name": 0.55, "vector": 0.25, "knn": 0.20}, "component weights of the fuzzy score"),
    "fuzzy_generic_tokens": (["operations", "operation", "opns", "sales", "international", "intl", "europe", "emea", "asia", "pacific",
                              "america", "americas", "north", "na", "group", "holding", "holdings", "trading", "manufacturing", "mfg",
                              "plant", "assembly", "division", "services", "svc", "solutions", "automotive", "industry", "computer",
                              "technologies", "technology", "electronics", "usa", "us", "china", "japan", "germany", "ireland"],
                             "tokens ignored when penalising extra words in an account name"),
    "segment_adi_cutoff": (1.32, "Syntetos-Boylan ADI cut-off"),
    "segment_cv2_cutoff": (0.49, "Syntetos-Boylan CV^2 cut-off"),
    "segment_seasonality_min": (0.70, "STL seasonal strength above which a series is Seasonal (STL is biased upward on ~36 noisy points, so also needs the ACF(12) check)"),
    "segment_seasonal_acf_min": (0.25, "minimum autocorrelation at lag 12 that must corroborate STL seasonal strength"),
    "segment_exog_min": (0.55, "exogenous-driver score above which a series is Complex/Multivariate"),
    "override_max_deviation_pct": (60.0, "overrides deviating more than this % from AI need a justification comment"),
    "override_requires_approval": (False, "if true, overrides only enter consensus once approved"),
    "champion_prior_tolerance": (0.02, "relative wMAPE gap within which the segment-prior model wins ties"),
    "fva_significance_level": (0.05, "two-sided level for FVA bootstrap confidence intervals"),
    "dq_block_on_error": (True, "ERROR-severity DQ failures block forecast runs"),
}


def get_setting(session: Session, key: str, default: Any = None) -> Any:
    row = session.get(SystemSetting, key)
    if row is not None:
        return row.value
    if key in DEFAULTS:
        return DEFAULTS[key][0]
    return default


def set_setting(session: Session, key: str, value: Any, user: str = "system") -> None:
    row = session.get(SystemSetting, key)
    if row is None:
        session.add(SystemSetting(key=key, value=value, description=DEFAULTS.get(key, (None, None))[1], updated_by=user))
    else:
        row.value, row.updated_by = value, user
    session.flush()


def seed_defaults(session: Session) -> None:
    for k, (v, d) in DEFAULTS.items():
        if session.get(SystemSetting, k) is None:
            session.add(SystemSetting(key=k, value=v, description=d, updated_by="system"))
    session.flush()
