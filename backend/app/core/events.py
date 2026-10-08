"""Live updates: PostgreSQL LISTEN/NOTIFY -> in-process hub -> Server-Sent Events.

Any process (API, Celery worker, CLI) calls `notify(...)`; every API process listens once and fans the event out to its
connected browsers. NOTIFY issued inside a transaction is delivered only on COMMIT, so clients never see uncommitted state."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging

import psycopg
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import db as _db
from app.core.config import get_settings

log = logging.getLogger(__name__)
CHANNEL = "oem_events"


def notify(session: Session, event: dict) -> None:
    """Queue an event on this session's transaction (delivered on commit)."""
    session.execute(text("SELECT pg_notify(:c, :p)"), {"c": CHANNEL, "p": json.dumps(event, default=str)[:7500]})


def notify_data_changed(session: Session) -> None:
    sc = _db.current_schema.get()
    if sc:
        notify(session, {"type": "data_changed", "schema": sc})


class Hub:
    def __init__(self) -> None:
        self.subs: set[asyncio.Queue] = set()
        self.task: asyncio.Task | None = None

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self.subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subs.discard(q)

    def publish(self, payload: str) -> None:
        for q in list(self.subs):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:  # slow client: drop it, it reconnects and refetches
                self.subs.discard(q)

    async def _listen(self) -> None:
        dsn = get_settings().database_url.replace("postgresql+psycopg://", "postgresql://", 1)
        while True:
            try:
                async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
                    await conn.execute(f"LISTEN {CHANNEL}")
                    log.info("event hub listening on %s", CHANNEL)
                    async for n in conn.notifies():
                        self.publish(n.payload)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("event hub reconnecting: %s", e)
                self.publish(json.dumps({"type": "resync"}))
                await asyncio.sleep(2)

    def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(self._listen(), name="event-hub")

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.task
            self.task = None


hub = Hub()
