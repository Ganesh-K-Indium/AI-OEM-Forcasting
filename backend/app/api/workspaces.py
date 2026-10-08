"""Workspace registry: create / list / rename / archive / delete isolated research contexts."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.aio import in_thread
from app.core import workspace as wsmod
from app.core.db import get_adb
from app.core.security import current_user, require_roles
from app.models.ops import Job, User, Workspace

router = APIRouter(prefix="/workspaces", tags=["workspaces"], dependencies=[Depends(current_user)])


class WorkspaceIn(BaseModel):
    name: str = Field(min_length=2, max_length=96)
    kind: str = "custom"
    description: str | None = None


class WorkspacePatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=96)
    description: str | None = None
    archived: bool | None = None


def _out(w: Workspace, job: Job | None = None) -> dict:
    return dict(id=w.id, slug=w.slug, name=w.name, description=w.description, kind=w.kind, status=w.status, schema_name=w.schema_name, source=w.source,
                capabilities=w.capabilities or {}, created_by=w.created_by, created_at=w.created_at,
                active_job=dict(id=job.id, type=job.job_type, state=job.state, progress=job.progress, message=job.message) if job else None)


@router.get("")
async def list_workspaces(include_archived: bool = False, db: AsyncSession = Depends(get_adb)):
    q = select(Workspace).order_by(Workspace.created_at)
    if not include_archived:
        q = q.where(Workspace.status != "ARCHIVED")
    rows = list((await db.execute(q)).scalars())
    jobs = {}
    for j in (await db.execute(select(Job).where(Job.state.in_(["PENDING", "RUNNING"])).order_by(Job.created_at))).scalars():
        jobs[j.workspace_id] = j
    return [_out(w, jobs.get(w.id)) for w in rows]


@router.post("", status_code=201)
async def create(body: WorkspaceIn, user: User = Depends(require_roles("planner"))):
    if body.kind not in wsmod.KINDS:
        raise HTTPException(422, f"kind must be one of {wsmod.KINDS}")

    def _do(s):
        return _out(wsmod.create_workspace(s, body.name, body.kind, body.description, user.email))

    try:
        return await in_thread(_do)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.patch("/{ws_id}")
async def patch(ws_id: str, body: WorkspacePatch, db: AsyncSession = Depends(get_adb), user: User = Depends(require_roles("planner"))):
    w = await db.get(Workspace, ws_id)
    if w is None:
        raise HTTPException(404, "workspace not found")
    if body.name:
        w.name = body.name.strip()
    if body.description is not None:
        w.description = body.description
    if body.archived is not None:
        w.status = "ARCHIVED" if body.archived else ("READY" if (w.capabilities or {}).get("has_mapped") else "EMPTY")
    await db.commit()
    wsmod.invalidate_cache()
    return _out(w)


@router.delete("/{ws_id}", status_code=204)
async def delete(ws_id: str, db: AsyncSession = Depends(get_adb), user: User = Depends(require_roles("admin"))):
    busy = (await db.execute(select(Job.id).where(Job.workspace_id == ws_id, Job.state.in_(["PENDING", "RUNNING"])).limit(1))).first()
    if busy:
        raise HTTPException(409, "a job is still running in this workspace")

    def _do(s):
        wsmod.delete_workspace(s, ws_id)

    try:
        await in_thread(_do)
    except KeyError as e:
        raise HTTPException(404, "workspace not found") from e
