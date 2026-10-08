from __future__ import annotations

import anyio.to_thread
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.db import get_adb
from app.core.security import create_token, current_user, verify_password
from app.models.ops import User
from app.schemas.common import LoginIn, TokenOut, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenOut)
async def login(body: LoginIn, db: AsyncSession = Depends(get_adb)):
    u = (await db.execute(select(User).where(User.email == body.email.lower(), User.is_active))).scalar_one_or_none()
    ok = await anyio.to_thread.run_sync(verify_password, body.password, u.password_hash if u else "$2b$10$invalidinvalidinvalidinvalidinvalidinvalidinvalidinvalidi")  # constant-ish time
    if u is None or not ok:
        raise HTTPException(401, "Invalid credentials")
    await db.run_sync(lambda s: audit(s, u.email, "LOGIN", "user", u.email))
    await db.commit()
    return TokenOut(access_token=create_token(u), user=UserOut.model_validate(u))


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(current_user)):
    return user
