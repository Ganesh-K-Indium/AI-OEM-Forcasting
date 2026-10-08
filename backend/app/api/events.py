"""GET /events - Server-Sent Events stream of job progress and data changes (see app/core/events.py)."""
from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials

from app.core.db import AsyncSessionLocal
from app.core.events import hub
from app.core.security import bearer, current_user

router = APIRouter(tags=["events"])


@router.get("/events")
async def events(cred: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
    async with AsyncSessionLocal(None) as db:  # authenticate, then release the DB connection before streaming
        await current_user(cred, db)
    q = hub.subscribe()

    async def gen():
        try:
            yield "retry: 3000\n: connected\n\n"
            while True:
                try:
                    yield f"data: {await asyncio.wait_for(q.get(), 15)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            hub.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
