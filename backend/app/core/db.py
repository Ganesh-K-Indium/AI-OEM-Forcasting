from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager

import numpy as np
from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, Text, TypeDecorator, create_engine, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

EMBED_DIM = get_settings().embedding_dim


class Base(DeclarativeBase):
    pass


class EmbeddingType(TypeDecorator):
    """pgvector `vector(N)` on PostgreSQL, JSON text elsewhere (tests / SQLite dev)."""

    impl = Text
    cache_ok = True

    def __init__(self, dim: int = EMBED_DIM):
        super().__init__()
        self.dim = dim

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(Vector(self.dim))
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        arr = np.asarray(value, dtype=float).tolist()
        return arr if dialect.name == "postgresql" else json.dumps(arr)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, str):
            return np.asarray(json.loads(value), dtype=float)
        return np.asarray(value, dtype=float)


JsonType = JSON


def _make_engine(url: str):
    kwargs: dict = {"future": True, "pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 60}
    eng = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(eng, "connect")
        def _pragmas(dbapi_conn, _):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return eng


def async_url(url: str) -> str:
    """Driver URL for the async engine: psycopg3 serves both sync and async; SQLite needs aiosqlite."""
    return url.replace("sqlite:///", "sqlite+aiosqlite:///", 1) if url.startswith("sqlite:") else url


def _make_async_engine(url: str):
    kw: dict = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kw["connect_args"] = {"timeout": 60}
    else:
        kw.update(pool_size=10, max_overflow=20, pool_recycle=1800)
    return create_async_engine(async_url(url), **kw)


engine = _make_engine(get_settings().database_url)  # sync: Celery workers, jobs, migrations, analytics threads
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
async_engine = _make_async_engine(get_settings().database_url)  # async: FastAPI request path
AsyncSessionLocal = async_sessionmaker(bind=async_engine, expire_on_commit=False, autoflush=False, class_=AsyncSession)


def configure_engine(url: str):
    """Re-point both engines (tests / CLI)."""
    global engine, async_engine
    engine.dispose()
    engine = _make_engine(url)
    SessionLocal.configure(bind=engine)
    async_engine = _make_async_engine(url)
    AsyncSessionLocal.configure(bind=async_engine)
    return engine


@contextmanager
def session_scope() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_db() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


async def get_adb():
    """Request-scoped AsyncSession (FastAPI dependency)."""
    async with AsyncSessionLocal() as s:
        yield s
