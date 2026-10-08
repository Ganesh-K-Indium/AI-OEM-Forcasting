from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.forecasting.service import NotFound, resolve_run
from app.models.forecast import ForecastRun


def run_or_404(db: Session, run_id: str | None) -> ForecastRun:
    try:
        return resolve_run(db, run_id)
    except NotFound as e:
        raise HTTPException(404, str(e)) from e
