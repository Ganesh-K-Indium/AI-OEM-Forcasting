from __future__ import annotations

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.db import current_schema, get_adb
from app.forecasting.service import NotFound, resolve_run
from app.models.forecast import ForecastRun


def run_or_404(db: Session, run_id: str | None) -> ForecastRun:
    try:
        return resolve_run(db, run_id)
    except NotFound as e:
        raise HTTPException(404, str(e)) from e


async def require_workspace(_db=Depends(get_adb)) -> str:
    """Router-level guard: the request must resolve to a workspace (X-Workspace header, or the oldest active one)."""
    sc = current_schema.get()
    if sc is None:
        raise HTTPException(409, "No workspace exists yet - create one under Workspaces")
    return sc
