from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.aio import anyio_hash, in_session
from app.core.audit import audit
from app.core.db import get_adb
from app.core.security import current_user, require_roles
from app.core.settings_store import DEFAULTS, get_setting, set_setting
from app.models.ops import DqResult, DriftReport, Job, User
from app.schemas.admin import DqOut, DriftOut, JobIn, JobOut, SettingIn, SettingOut, UserIn
from app.schemas.common import UserOut
from app.tasks.jobs import JOB_TYPES, submit_job

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(current_user)])


def _job(j: Job) -> JobOut:
    return JobOut.model_validate(j, from_attributes=True)


def _submit(s, job_type, params, email):
    try:
        return _job(submit_job(s, job_type, params, email))
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


@router.post("/jobs", response_model=JobOut, status_code=202)
async def create_job(body: JobIn, db: AsyncSession = Depends(get_adb), user: User = Depends(require_roles("planner"))):
    if body.job_type == "seed_demo" and user.role != "admin":
        raise HTTPException(403, "only admins can (re)seed demo data")
    return await in_session(db, _submit, body.job_type, body.params, user.email)


@router.get("/jobs", response_model=list[JobOut])
async def jobs(limit: int = 30, db: AsyncSession = Depends(get_adb)):
    return [_job(j) for j in (await db.execute(select(Job).order_by(Job.created_at.desc()).limit(limit))).scalars()]


@router.get("/jobs/{job_id}", response_model=JobOut)
async def job(job_id: str, db: AsyncSession = Depends(get_adb)):
    j = await db.get(Job, job_id)
    if j is None:
        raise HTTPException(404, "job not found")
    await db.refresh(j)
    return _job(j)


@router.get("/job-types")
async def job_types():
    return list(JOB_TYPES)


@router.get("/dq", response_model=list[DqOut])
async def dq(db: AsyncSession = Depends(get_adb)):
    latest = (await db.execute(select(DqResult.batch_id).order_by(DqResult.id.desc()).limit(1))).scalar()
    if latest is None:
        return []
    rows = (await db.execute(select(DqResult).where(DqResult.batch_id == latest).order_by(DqResult.id))).scalars()
    return [DqOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/drift", response_model=list[DriftOut])
async def drift(run_id: str | None = None, db: AsyncSession = Depends(get_adb)):
    q = select(DriftReport).order_by(DriftReport.id.desc()).limit(60)
    if run_id:
        q = q.where(DriftReport.run_id == run_id)
    return [DriftOut.model_validate(d, from_attributes=True) for d in (await db.execute(q)).scalars()]


def _settings(s):
    return [SettingOut(key=k, value=get_setting(s, k), description=d) for k, (_, d) in DEFAULTS.items()]


@router.get("/settings", response_model=list[SettingOut])
async def settings(db: AsyncSession = Depends(get_adb)):
    return await in_session(db, _settings)


def _put(s, email, key, value):
    before = get_setting(s, key)
    set_setting(s, key, value, email)
    audit(s, email, "SETTING_UPDATE", "setting", key, before, value)
    return SettingOut(key=key, value=value, description=DEFAULTS[key][1])


@router.put("/settings/{key}", response_model=SettingOut)
async def put_setting(key: str, body: SettingIn, db: AsyncSession = Depends(get_adb), user: User = Depends(require_roles("admin"))):
    if key not in DEFAULTS:
        raise HTTPException(404, "unknown setting")
    return await in_session(db, _put, user.email, key, body.value, commit=True)


@router.get("/users", response_model=list[UserOut])
async def users(db: AsyncSession = Depends(get_adb), user: User = Depends(require_roles("admin"))):
    return list((await db.execute(select(User).order_by(User.email))).scalars())


@router.post("/users", response_model=UserOut, status_code=201)
async def create_user(body: UserIn, db: AsyncSession = Depends(get_adb), user: User = Depends(require_roles("admin"))):
    if (await db.execute(select(User).where(User.email == body.email.lower()))).first():
        raise HTTPException(409, "user exists")
    pw = await anyio_hash(body.password)
    u = User(email=body.email.lower(), full_name=body.full_name, role=body.role, password_hash=pw, scope_oems=body.scope_oems, scope_regions=body.scope_regions)
    db.add(u)
    await db.flush()
    await db.run_sync(lambda s: audit(s, user.email, "USER_CREATE", "user", u.email, None, dict(role=u.role)))
    await db.commit()
    return u
