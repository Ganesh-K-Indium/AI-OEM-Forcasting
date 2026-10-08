"""Background jobs: Celery+Redis when USE_CELERY=true, otherwise an in-process daemon thread (single-node / dev)."""
from __future__ import annotations

import logging
import time
import threading
import traceback
import uuid
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.events import notify
from app.core.db import SessionLocal, current_schema, use_workspace
from app.models.facts import MappedSeries
from app.models.forecast import ForecastRun
from app.models.ops import Job, Workspace

log = logging.getLogger(__name__)
JOB_TYPES = ("seed_demo", "import_dataset", "run_forecast", "mapping_pipeline", "materialize", "refresh_risk", "compute_fva", "run_dq")
EXCLUSIVE = ("seed_demo", "import_dataset", "run_forecast")  # one at a time per workspace


def submit_job(session: Session, job_type: str, params: dict | None, user: str, workspace_id: str | None = None) -> Job:
    if job_type not in JOB_TYPES:
        raise ValueError(f"unknown job type {job_type}")
    if workspace_id is None:
        raise ValueError("no workspace selected")
    busy = session.execute(select(Job).where(Job.workspace_id == workspace_id, Job.job_type.in_(EXCLUSIVE), Job.state.in_(["PENDING", "RUNNING"]))).scalars().first()
    if busy and job_type in EXCLUSIVE:
        raise ValueError(f"a {busy.job_type} job is already {busy.state.lower()} in this workspace ({busy.id})")
    job = Job(id=str(uuid.uuid4()), job_type=job_type, params=params or {}, created_by=user, state="PENDING", workspace_id=workspace_id)
    session.add(job)
    session.commit()
    if get_settings().use_celery:
        from app.tasks.celery_app import celery_app

        celery_app.send_task("oem.run_job", args=[job.id])
    else:
        threading.Thread(target=execute_job, args=(job.id,), daemon=True, name=f"job-{job.id[:8]}").start()
    return job


def _update(job_id: str, **kw) -> None:
    with SessionLocal() as s:
        j = s.get(Job, job_id)
        if j is None:
            return
        for k, v in kw.items():
            setattr(j, k, v)
        notify(s, {"type": "job", "workspace_id": j.workspace_id, "job_id": j.id, "job_type": j.job_type, "state": j.state, "progress": j.progress, "message": j.message})
        s.commit()


def execute_job(job_id: str) -> None:
    with SessionLocal(None) as s:
        job = s.get(Job, job_id)
        jt, params, user = job.job_type, dict(job.params or {}), job.created_by or "system"
        ws = s.get(Workspace, job.workspace_id) if job.workspace_id else None
        schema = ws.schema_name if ws else None
    with use_workspace(schema):
        _execute(job_id, jt, params, user, ws.id if ws else None)


def _execute(job_id: str, jt: str, params: dict, user: str, ws_id: str | None) -> None:
    _update(job_id, state="RUNNING", started_at=datetime.utcnow(), progress=0.01, message="started")

    t0 = time.time()
    log.info("job %s [%s] started params=%s", job_id[:8], jt, params)

    def progress(frac: float, msg: str) -> None:
        log.info("job %s [%s] %3.0f%% %s (+%ds)", job_id[:8], jt, frac * 100, msg, time.time() - t0)
        _update(job_id, progress=float(min(frac, 0.99)), message=msg)

    try:
        with SessionLocal() as s:
            result = HANDLERS[jt](s, params, user, progress)
            s.commit()
        if ws_id and jt in ("seed_demo", "import_dataset", "mapping_pipeline", "materialize", "run_forecast"):
            from app.core.workspace import refresh_capabilities

            refresh_capabilities(current_schema.get(), ws_id)
        log.info("job %s [%s] finished in %ds", job_id[:8], jt, time.time() - t0)
        _update(job_id, state="SUCCESS", progress=1.0, message="done", result=result, finished_at=datetime.utcnow())
    except Exception as e:  # noqa: BLE001
        log.exception("job %s failed", job_id)
        if ws_id and jt in ("seed_demo", "import_dataset"):
            from app.core.workspace import set_status

            set_status(ws_id, "FAILED")
        _update(job_id, state="FAILED", error=f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=6)}", finished_at=datetime.utcnow())


def _latest_cycle(s: Session) -> date:
    return s.execute(select(MappedSeries.month).order_by(MappedSeries.month.desc()).limit(1)).scalar_one()


def _h_seed(s, p, user, progress):
    from app.data.seed import seed_demo

    return seed_demo(s, progress, replay_cycles=int(p.get("replay_cycles", 6)), fast=bool(p.get("fast", False)))


def _h_import(s, p, user, progress):
    from app.data.importer import run_import

    return run_import(s, p, user, progress)


def _h_forecast(s, p, user, progress):
    from app.forecasting.pipeline import run_forecast
    from app.governance.cycles import attach_run

    cm = date.fromisoformat(p["cycle_month"]) if p.get("cycle_month") else _latest_cycle(s)
    rid = run_forecast(s, cm, kind="CURRENT", mode=p.get("mode", "full"), user=user, progress=progress)
    attach_run(s, rid)
    return {"run_id": rid, "cycle_month": str(cm)}


def _h_mapping(s, p, user, progress):
    from app.mapping.service import materialize_mapped_series, run_mapping_pipeline

    r = run_mapping_pipeline(s, user)
    r["materialized"] = materialize_mapped_series(s)
    return r


def _h_materialize(s, p, user, progress):
    from app.mapping.service import materialize_mapped_series

    return materialize_mapped_series(s)


def _h_risk(s, p, user, progress):
    from app.risk.engine import refresh_alerts

    rid = p.get("run_id") or s.execute(select(ForecastRun.id).where(ForecastRun.status == "COMPLETED").order_by(ForecastRun.created_at.desc())).scalars().first()
    return {"alerts": refresh_alerts(s, rid), "run_id": rid}


def _h_fva(s, p, user, progress):
    from app.governance.fva import compute_fva

    return {"rows": compute_fva(s)}


def _h_dq(s, p, user, progress):
    from app.quality.checks import run_dq

    r = run_dq(s, _latest_cycle(s))
    return {"checks": len(r), "failed": sum(1 for x in r if not x["passed"])}


HANDLERS = {"seed_demo": _h_seed, "import_dataset": _h_import, "run_forecast": _h_forecast, "mapping_pipeline": _h_mapping, "materialize": _h_materialize,
            "refresh_risk": _h_risk, "compute_fva": _h_fva, "run_dq": _h_dq}
