"""Background jobs: Celery+Redis when USE_CELERY=true, otherwise an in-process daemon thread (single-node / dev)."""
from __future__ import annotations

import logging
import threading
import traceback
import uuid
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models.facts import MappedSeries
from app.models.forecast import ForecastRun
from app.models.ops import Job

log = logging.getLogger(__name__)
JOB_TYPES = ("seed_demo", "run_forecast", "mapping_pipeline", "materialize", "refresh_risk", "compute_fva", "run_dq")


def submit_job(session: Session, job_type: str, params: dict | None, user: str) -> Job:
    if job_type not in JOB_TYPES:
        raise ValueError(f"unknown job type {job_type}")
    running = session.execute(select(Job).where(Job.job_type == job_type, Job.state.in_(["PENDING", "RUNNING"]))).scalars().first()
    if running and job_type in ("seed_demo", "run_forecast"):
        raise ValueError(f"a {job_type} job is already {running.state.lower()} ({running.id})")
    job = Job(id=str(uuid.uuid4()), job_type=job_type, params=params or {}, created_by=user, state="PENDING")
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
        for k, v in kw.items():
            setattr(j, k, v)
        s.commit()


def execute_job(job_id: str) -> None:
    with SessionLocal() as s:
        job = s.get(Job, job_id)
        jt, params, user = job.job_type, dict(job.params or {}), job.created_by or "system"
    _update(job_id, state="RUNNING", started_at=datetime.utcnow(), progress=0.01, message="started")

    def progress(frac: float, msg: str) -> None:
        _update(job_id, progress=float(min(frac, 0.99)), message=msg)

    try:
        with SessionLocal() as s:
            result = HANDLERS[jt](s, params, user, progress)
            s.commit()
        _update(job_id, state="SUCCESS", progress=1.0, message="done", result=result, finished_at=datetime.utcnow())
    except Exception as e:  # noqa: BLE001
        log.exception("job %s failed", job_id)
        _update(job_id, state="FAILED", error=f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=6)}", finished_at=datetime.utcnow())


def _latest_cycle(s: Session) -> date:
    return s.execute(select(MappedSeries.month).order_by(MappedSeries.month.desc()).limit(1)).scalar_one()


def _h_seed(s, p, user, progress):
    from app.data.seed import seed_demo

    return seed_demo(s, progress, replay_cycles=int(p.get("replay_cycles", 6)), fast=bool(p.get("fast", False)))


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


HANDLERS = {"seed_demo": _h_seed, "run_forecast": _h_forecast, "mapping_pipeline": _h_mapping, "materialize": _h_materialize,
            "refresh_risk": _h_risk, "compute_fva": _h_fva, "run_dq": _h_dq}
