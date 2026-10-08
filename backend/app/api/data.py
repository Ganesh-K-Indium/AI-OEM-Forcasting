"""Data tab: presets, uploads, column mapping, validation and import jobs for the active workspace."""
from __future__ import annotations

import io
import re
import shutil
import uuid
from pathlib import Path

import anyio.to_thread
from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.aio import in_session, in_thread
from app.api.deps import require_workspace
from app.core.config import get_settings
from app.core.db import get_adb
from app.core.security import current_user, require_roles
from app.core.workspace import compute_capabilities, set_status
from app.data import importer
from app.data.m5 import m5_available
from app.models.ops import Job, User
from app.tasks.jobs import submit_job

router = APIRouter(prefix="/data", tags=["data"], dependencies=[Depends(current_user), Depends(require_workspace)])
MAX_UPLOAD_MB = 800
TEMPLATES = {
    "sales": "month,customer,product,units,revenue,region,oem\n2024-01-01,Acme Distribution,WIDGET-A,120,36000,EMEA,Acme\n2024-01-01,Globex Corp,WIDGET-B,80,30400,AMER,Globex\n",
    "mapping": "customer,oem,region,allocation_pct\nAcme Distribution,Acme,EMEA,0.6\nAcme Distribution,Initech,EMEA,0.4\n",
    "backlog": "snapshot_month,delivery_month,customer,product,units,value\n2024-12-01,2025-01-01,Acme Distribution,WIDGET-A,60,18000\n",
    "capacity": "month,region,product,capacity_units\n2025-01-01,EMEA,WIDGET-A,500\n",
}


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).name)[:80] or "file"


@router.get("/status")
async def status(db: AsyncSession = Depends(get_adb)):
    ws = db.info["workspace"]
    caps = await in_session(db, compute_capabilities)
    job = (await db.execute(select(Job).where(Job.workspace_id == ws.id, Job.job_type.in_(["import_dataset", "seed_demo"])).order_by(Job.created_at.desc()).limit(1))).scalars().first()
    return dict(workspace=dict(id=ws.id, slug=ws.slug, name=ws.name, kind=ws.kind, status=ws.status), capabilities=caps,
                presets=dict(synthetic=dict(available=True), m5=m5_available(get_settings().import_dir / "m5")),
                last_job=dict(id=job.id, type=job.job_type, state=job.state, progress=job.progress, message=job.message, error=job.error, result=job.result) if job else None,
                fields={k: v for k, v in importer.FIELDS.items()})


@router.get("/templates/{role}")
async def template(role: str):
    if role not in TEMPLATES:
        raise HTTPException(404, "unknown template")
    return Response(TEMPLATES[role], media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{role}_template.csv"'})


@router.post("/upload")
async def upload(role: str = "sales", file: UploadFile = File(...), db: AsyncSession = Depends(get_adb), user: User = Depends(require_roles("admin"))):
    if role not in importer.FIELDS:
        raise HTTPException(422, "role must be one of " + ", ".join(importer.FIELDS))
    ws = db.info["workspace"]
    stored = f"{uuid.uuid4().hex[:8]}_{_safe(file.filename or 'upload.csv')}"
    dest = importer.upload_dir(ws.id) / stored

    def _save():
        with open(dest, "wb") as out:
            shutil.copyfileobj(file.file, out, 1024 * 1024)

    await anyio.to_thread.run_sync(_save)
    if dest.stat().st_size > MAX_UPLOAD_MB * 1024 * 1024:
        dest.unlink(missing_ok=True)
        raise HTTPException(413, f"file larger than {MAX_UPLOAD_MB} MB - place it in the server import folder instead")

    def _peek():
        df = importer.read_table(dest, nrows=2000)
        return df

    try:
        df = await anyio.to_thread.run_sync(_peek)
    except Exception as e:  # noqa: BLE001
        dest.unlink(missing_ok=True)
        raise HTTPException(422, f"could not read the file: {e}") from e
    return dict(file=stored, name=file.filename, role=role, size=dest.stat().st_size, columns=[str(c) for c in df.columns],
                dtypes={str(c): str(t) for c, t in df.dtypes.items()}, preview=df.head(6).astype(str).to_dict("records"),
                suggested=importer.suggest_mapping(role, [str(c) for c in df.columns]), fields=importer.FIELDS[role])


class Spec(BaseModel):
    file: str
    map: dict[str, str | None] | None = None


class ImportIn(BaseModel):
    source: str = "files"  # files | m5
    sales: Spec | None = None
    mapping: Spec | None = None
    backlog: Spec | None = None
    capacity: Spec | None = None
    options: dict | None = None


def _path(ws_id: str, spec: Spec) -> Path:
    p = importer.upload_dir(ws_id) / Path(spec.file).name
    if not p.exists():
        raise HTTPException(404, f"uploaded file {spec.file} not found - upload it again")
    return p


@router.post("/validate")
async def validate(body: ImportIn, db: AsyncSession = Depends(get_adb)):
    if body.sales is None:
        raise HTTPException(422, "a sales file is required")
    ws = db.info["workspace"]
    p = _path(ws.id, body.sales)
    try:
        return await anyio.to_thread.run_sync(importer.validate_sales, p, body.sales.map or {}, body.options or {})
    except importer.ImportError_ as e:
        return dict(ok=False, issues=[dict(level="error", message=str(e))], summary={}, preview=[])


def _submit(s, params, email, ws_id):
    try:
        job = submit_job(s, "import_dataset", params, email, ws_id)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return job.id


@router.post("/import", status_code=202)
async def start_import(body: ImportIn, db: AsyncSession = Depends(get_adb), user: User = Depends(require_roles("admin"))):
    ws = db.info["workspace"]
    if ws.kind == "synthetic":
        raise HTTPException(409, "this is a synthetic workspace - create a Custom or M5 workspace to import data")
    params: dict = dict(source=body.source, workspace_id=ws.id, options=body.options or {})
    if body.source == "m5":
        if ws.kind != "m5":
            raise HTTPException(409, "the M5 preset can only be loaded into an M5 workspace")
        params["m5_dir"] = str(get_settings().import_dir / "m5")
    else:
        if body.sales is None:
            raise HTTPException(422, "a sales file is required")
        for role in ("sales", "mapping", "backlog", "capacity"):
            spec = getattr(body, role)
            if spec:
                _path(ws.id, spec)
                params[role] = dict(file=Path(spec.file).name, map=spec.map)
    job_id = await in_session(db, _submit, params, user.email, ws.id)
    set_status(ws.id, "IMPORTING")
    return dict(job_id=job_id)


@router.post("/clear", status_code=204)
async def clear(db: AsyncSession = Depends(get_adb), user: User = Depends(require_roles("admin"))):
    from app.core.workspace import init_defaults, refresh_capabilities

    ws = db.info["workspace"]

    def _do(s):
        from app.data.loader import wipe_domain_data

        wipe_domain_data(s)
        s.commit()

    await in_thread(_do)
    init_defaults(ws.schema_name)
    await anyio.to_thread.run_sync(refresh_capabilities, ws.schema_name, ws.id)
