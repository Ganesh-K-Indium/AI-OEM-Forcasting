"""Async plumbing. Rule of thumb used across the API:
   * I/O-bound CRUD      -> `await in_session(db, fn, ...)`  : AsyncSession (async driver), no thread, loop never blocks on the database
   * CPU-bound analytics -> `await in_thread(fn, ...)`        : worker thread with its own sync Session, so pandas/numpy never stall the event loop
   * Heavy compute (forecasts, seeding, mapping rebuilds) -> Celery/job workers (never in the request path)."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import anyio.to_thread
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal
from app.models.ops import User


@dataclass(frozen=True)
class UserCtx:
    """Detached, thread-safe snapshot of the authenticated user."""

    id: int
    email: str
    role: str
    scope_oems: list[str] | None
    scope_regions: list[str] | None
    full_name: str = ""

    @classmethod
    def of(cls, u: User) -> UserCtx:
        return cls(u.id, u.email, u.role, list(u.scope_oems or []) or None, list(u.scope_regions or []) or None, u.full_name)


async def in_session(db: AsyncSession, fn: Callable[..., Any], *args, commit: bool = False, **kw) -> Any:
    """Run sync service code against the request's AsyncSession (greenlet bridge, non-blocking DB I/O)."""
    def _call(s):
        out = fn(s, *args, **kw)
        if commit:
            s.commit()
        return out

    try:
        return await db.run_sync(_call)
    except Exception:
        await db.rollback()
        raise


def _in_new_session(fn: Callable[..., Any], commit: bool, args: tuple, kw: dict) -> Any:
    with SessionLocal() as s:
        try:
            out = fn(s, *args, **kw)
            if commit:
                s.commit()
            return out
        except Exception:
            s.rollback()
            raise


async def in_thread(fn: Callable[..., Any], *args, commit: bool = False, **kw) -> Any:
    """Run sync (CPU-heavy) service code in a worker thread with a dedicated Session."""
    return await anyio.to_thread.run_sync(lambda: _in_new_session(fn, commit, args, kw))


async def anyio_hash(password: str) -> str:
    """bcrypt is CPU-bound: keep it off the event loop."""
    from app.core.security import hash_password

    return await anyio.to_thread.run_sync(hash_password, password)
