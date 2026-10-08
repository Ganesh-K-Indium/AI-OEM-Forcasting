"""Workspaces: one isolated PostgreSQL schema per research / dataset context.

`users`, `workspaces` and `jobs` are shared (public). Every other table is created inside the workspace schema, so a
workspace can be created, cloned-by-reseeding or dropped without touching any other one."""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass

import anyio.to_thread
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core import db as _db
from app.core.db import SessionLocal, check_schema, engine, engine_for, forget_schema, use_workspace, workspace_tables
from app.models.ops import Workspace

KINDS = ("synthetic", "m5", "custom")
SCHEMA_VERSION = 1
_TTL = 5.0


@dataclass(frozen=True)
class WorkspaceInfo:
    id: str
    slug: str
    name: str
    kind: str
    schema_name: str
    status: str

    @classmethod
    def of(cls, w: Workspace) -> WorkspaceInfo:
        return cls(w.id, w.slug, w.name, w.kind, w.schema_name, w.status)


_cache: dict[str | None, tuple[float, WorkspaceInfo | None]] = {}


def invalidate_cache() -> None:
    _cache.clear()
    _announce()


def _announce() -> None:
    """Tell every open browser that the workspace list changed (create / delete / rename / status)."""
    from app.core.events import notify

    try:
        with SessionLocal(None) as s:
            notify(s, {"type": "workspaces"})
            s.commit()
    except Exception:  # noqa: BLE001 - best effort, never block a workspace change
        pass


def _lookup(session: Session, slug: str | None) -> WorkspaceInfo | None:
    q = select(Workspace).where(Workspace.status != "ARCHIVED")
    q = q.where(Workspace.slug == slug) if slug else q.order_by(Workspace.created_at, Workspace.slug).limit(1)
    w = session.execute(q).scalars().first()
    return WorkspaceInfo.of(w) if w else None


async def resolve_workspace(slug: str | None) -> WorkspaceInfo | None:
    """Slug -> workspace (cached a few seconds). No slug -> the oldest active workspace. Unknown slug -> 404."""
    from fastapi import HTTPException

    hit = _cache.get(slug)
    if hit and time.monotonic() - hit[0] < _TTL:
        info = hit[1]
    else:
        def _q():
            with SessionLocal(None) as s:
                return _lookup(s, slug)

        info = await anyio.to_thread.run_sync(_q)
        _cache[slug] = (time.monotonic(), info)
    if info is None and slug:
        raise HTTPException(404, f"workspace '{slug}' not found")
    return info


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]
    return s or "workspace"


def _schema_of(slug: str) -> str:
    return check_schema("ws_" + slug.replace("-", "_"))


def ensure_extensions() -> None:
    with engine.begin() as c:
        c.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))


def create_workspace(session: Session, name: str, kind: str = "custom", description: str | None = None, user: str = "system") -> Workspace:
    """Create the registry row, the schema, all workspace tables, and the workspace's default settings/rules/thresholds."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    name = name.strip()
    if len(name) < 2:
        raise ValueError("workspace name is too short")
    base = slugify(name)
    slug, n = base, 1
    while session.execute(select(Workspace.id).where(Workspace.slug == slug)).first():
        n += 1
        slug = f"{base}-{n}"
    w = Workspace(id=str(uuid.uuid4()), slug=slug, name=name, description=description, kind=kind, schema_name=_schema_of(slug), status="EMPTY",
                  schema_version=SCHEMA_VERSION, created_by=user, capabilities={})
    try:
        provision_schema(w.schema_name)  # schema first: the registry row only appears once the workspace is usable
    except Exception:
        drop_schema(w.schema_name)
        raise
    session.add(w)
    session.commit()
    invalidate_cache()
    return w


def provision_schema(schema: str) -> None:
    check_schema(schema)
    with engine.begin() as c:
        c.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
    eng = engine_for(schema)
    _db.Base.metadata.create_all(eng, tables=workspace_tables())
    with eng.begin() as c:  # approximate-NN indexes for entity resolution
        c.execute(text("CREATE INDEX IF NOT EXISTS ix_accounts_embedding_hnsw ON accounts USING hnsw (embedding vector_cosine_ops)"))
        c.execute(text("CREATE INDEX IF NOT EXISTS ix_oem_aliases_embedding_hnsw ON oem_aliases USING hnsw (embedding vector_cosine_ops)"))
    init_defaults(schema)


def init_defaults(schema: str) -> None:
    from app.core.settings_store import seed_defaults
    from app.mapping.rules import seed_default_rules
    from app.risk.engine import seed_default_thresholds

    with use_workspace(schema), SessionLocal() as s:
        seed_defaults(s)
        seed_default_rules(s)
        seed_default_thresholds(s)
        s.commit()


def reset_workspace(schema: str) -> None:
    """Drop and re-create an empty workspace schema (used before a re-import / re-seed)."""
    drop_schema(schema)
    provision_schema(schema)


def drop_schema(schema: str) -> None:
    check_schema(schema)
    forget_schema(schema)
    with engine.begin() as c:
        c.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


def delete_workspace(session: Session, ws_id: str) -> None:
    w = session.get(Workspace, ws_id)
    if w is None:
        raise KeyError(ws_id)
    drop_schema(w.schema_name)
    session.delete(w)
    session.commit()
    invalidate_cache()


def set_status(ws_id: str, status: str, **fields) -> None:
    with SessionLocal(None) as s:
        w = s.get(Workspace, ws_id)
        if w is None:
            return
        w.status = status
        for k, v in fields.items():
            setattr(w, k, v)
        s.commit()
    invalidate_cache()


def compute_capabilities(session: Session) -> dict:
    """What data the active workspace holds - pages and engines adapt to this instead of assuming a full ERP+CRM estate."""
    from app.models.facts import BacklogSnapshot, CapacityAllocation, Contract, MappedSeries, Opportunity, OpportunitySnapshot, SalesActual
    from app.models.forecast import ForecastRun
    from app.models.reference import Account, Oem, ProductLine, Region

    def n(model, *where):
        return int(session.execute(select(func.count()).select_from(model).where(*where)).scalar() or 0)

    lo, hi = session.execute(select(func.min(MappedSeries.month), func.max(MappedSeries.month))).one()
    c = dict(sales=n(SalesActual), mapped_rows=n(MappedSeries), backlog=n(BacklogSnapshot), capacity=n(CapacityAllocation), opportunities=n(Opportunity),
             opportunity_snapshots=n(OpportunitySnapshot), contracts=n(Contract), accounts=n(Account), oems=n(Oem), regions=n(Region), products=n(ProductLine),
             forecast_runs=n(ForecastRun, ForecastRun.status == "COMPLETED"), first_month=str(lo) if lo else None, last_month=str(hi) if hi else None)
    c.update(has_sales=c["sales"] > 0, has_backlog=c["backlog"] > 0, has_capacity=c["capacity"] > 0, has_crm=c["opportunity_snapshots"] > 0,
             has_contracts=c["contracts"] > 0, has_forecast=c["forecast_runs"] > 0, has_mapped=c["mapped_rows"] > 0)
    c["features"] = dict(
        forecast=c["has_mapped"], reconciliation=c["has_mapped"], revenue=c["has_mapped"], governance=c["has_forecast"],
        commercial_uplift=c["has_crm"], coverage_risk=c["has_backlog"], supply_risk=c["has_capacity"], pipeline_risk=c["has_crm"],
        contract_asp=c["has_contracts"], entity_resolution=c["accounts"] > c["oems"])
    return c


def refresh_capabilities(schema: str, ws_id: str) -> dict:
    with use_workspace(schema), SessionLocal() as s:
        caps = compute_capabilities(s)
    set_status(ws_id, "READY" if caps["has_mapped"] else "EMPTY", capabilities=caps)
    return caps


def workspace_kind(session: Session) -> str:
    """Kind (synthetic | m5 | custom) of the workspace this session is bound to."""
    from app.core.db import current_schema

    return session.execute(select(Workspace.kind).where(Workspace.schema_name == current_schema.get())).scalar() or "custom"
