from __future__ import annotations

import re
from collections.abc import Iterator
from contextvars import ContextVar
from contextlib import contextmanager

import numpy as np
from pgvector.sqlalchemy import Vector
from fastapi import Request
from sqlalchemy import JSON, Engine, TypeDecorator, create_engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session

from app.core.config import get_settings

EMBED_DIM = get_settings().embedding_dim


class Base(DeclarativeBase):
    pass


class EmbeddingType(TypeDecorator):
    """pgvector `vector(N)`; values are numpy arrays in Python."""

    impl = Vector
    cache_ok = True

    def __init__(self, dim: int = EMBED_DIM):
        super().__init__(dim)
        self.dim = dim

    def process_bind_param(self, value, dialect):
        return None if value is None else np.asarray(value, dtype=float).tolist()

    def process_result_value(self, value, dialect):
        return None if value is None else np.asarray(value, dtype=float)


JsonType = JSON


SHARED_TABLES = frozenset({"users", "workspaces", "jobs"})  # live in `public`; everything else lives in one schema per workspace
_SCHEMA_RE = re.compile(r"^ws_[a-z0-9_]{1,48}$")

# schema of the workspace the current request / job operates on (None -> shared tables only)
current_schema: ContextVar[str | None] = ContextVar("current_schema", default=None)
_URL = get_settings().database_url
_sync: dict[str | None, Engine] = {}
_async: dict[str | None, AsyncEngine] = {}


def check_schema(schema: str) -> str:
    if not _SCHEMA_RE.match(schema):
        raise ValueError(f"invalid workspace schema name: {schema!r}")
    return schema


def _opts(schema: str | None) -> dict:
    kw: dict = {"pool_pre_ping": True, "pool_recycle": 1800}
    if schema:
        kw["connect_args"] = {"options": f"-c search_path={check_schema(schema)},public"}
        kw.update(pool_size=4, max_overflow=8)
    else:
        kw.update(pool_size=6, max_overflow=14)
    return kw


def engine_for(schema: str | None = None) -> Engine:
    """Sync engine whose connections resolve unqualified tables in `schema` first, then `public`."""
    if schema not in _sync:
        _sync[schema] = create_engine(_URL, future=True, **_opts(schema))
    return _sync[schema]


def async_engine_for(schema: str | None = None) -> AsyncEngine:
    if schema not in _async:
        _async[schema] = create_async_engine(_URL, **_opts(schema))
    return _async[schema]


def forget_schema(schema: str) -> None:
    """Dispose cached engines of a (dropped) workspace schema."""
    e = _sync.pop(schema, None)
    if e is not None:
        e.dispose()
    _async.pop(schema, None)


class _Factory:
    """`SessionLocal()` -> Session bound to the active workspace (contextvar) unless `schema=` is given."""

    def __call__(self, schema: str | None | object = ...) -> Session:
        sc = current_schema.get() if schema is ... else schema
        return Session(bind=engine_for(sc), expire_on_commit=False, autoflush=False)


class _AsyncFactory:
    def __call__(self, schema: str | None | object = ...) -> AsyncSession:
        sc = current_schema.get() if schema is ... else schema
        return AsyncSession(bind=async_engine_for(sc), expire_on_commit=False, autoflush=False)


SessionLocal = _Factory()
AsyncSessionLocal = _AsyncFactory()
engine = engine_for(None)  # shared (public) engine: users, workspaces, jobs, migrations, bootstrap
async_engine = async_engine_for(None)


def configure_engine(url: str):
    """Re-point every engine at another database (tests / CLI)."""
    global _URL, engine, async_engine
    for e in list(_sync.values()):
        e.dispose()
    _sync.clear()
    _async.clear()
    _URL = url
    engine, async_engine = engine_for(None), async_engine_for(None)
    return engine


def dispose_all() -> None:
    for e in list(_sync.values()):
        e.dispose()


async def dispose_all_async() -> None:
    for e in list(_async.values()):
        await e.dispose()


@contextmanager
def use_workspace(schema: str | None):
    tok = current_schema.set(schema)
    try:
        yield
    finally:
        current_schema.reset(tok)


@contextmanager
def session_scope(schema: str | None | object = ...) -> Iterator[Session]:
    s = SessionLocal(schema)
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def workspace_tables():
    return [t for t in Base.metadata.sorted_tables if t.name not in SHARED_TABLES]


def shared_tables():
    return [t for t in Base.metadata.sorted_tables if t.name in SHARED_TABLES]


def get_db() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


async def get_adb(request: Request):
    """Request-scoped AsyncSession bound to the workspace named by the `X-Workspace` header (or `?workspace=`)."""
    from app.core.workspace import resolve_workspace

    ws = await resolve_workspace(request.headers.get("x-workspace") or request.query_params.get("workspace"))
    schema = ws.schema_name if ws else None
    current_schema.set(schema)  # per-request task context; worker threads started with anyio inherit it
    async with AsyncSessionLocal(schema) as s:
        s.info["workspace"] = ws
        yield s
