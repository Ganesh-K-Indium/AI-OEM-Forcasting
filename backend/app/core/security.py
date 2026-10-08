"""Authentication & RBAC: local JWT (bcrypt) or external OIDC (JWKS-verified bearer tokens)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated

import anyio.to_thread
import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_adb
from app.models.ops import User

ROLES = ("admin", "planner", "sales_rep", "steward", "viewer")
bearer = HTTPBearer(auto_error=False)


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=10)).decode()


def verify_password(pw: str, hashed: str | None) -> bool:
    return bool(hashed) and bcrypt.checkpw(pw.encode(), hashed.encode())


def create_token(user: User) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    payload = {"sub": user.email, "role": user.role, "iat": now, "exp": now + timedelta(minutes=s.access_token_minutes)}
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)


_jwks_client = None


def _decode_oidc(token: str) -> dict:
    global _jwks_client
    s = get_settings()
    if _jwks_client is None:
        _jwks_client = jwt.PyJWKClient(s.oidc_jwks_url)
    key = _jwks_client.get_signing_key_from_jwt(token).key
    return jwt.decode(token, key, algorithms=["RS256", "ES256"], audience=s.oidc_audience, issuer=s.oidc_issuer)


def decode_token(token: str) -> dict:
    s = get_settings()
    return _decode_oidc(token) if s.auth_mode == "oidc" else jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])


async def current_user(cred: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)], db: AsyncSession = Depends(get_adb)) -> User:
    if cred is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    try:  # JWKS fetch / crypto are blocking -> worker thread
        claims = await anyio.to_thread.run_sync(decode_token, cred.credentials)
    except jwt.PyJWTError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid token: {e}") from e
    email = claims.get("email") or claims.get("sub")
    user = (await db.execute(select(User).where(User.email == email, User.is_active))).scalar_one_or_none()
    if user is None and get_settings().auth_mode == "oidc":  # JIT-provision from the IdP role claim
        role = claims.get(get_settings().oidc_role_claim) or ["viewer"]
        role = role[0] if isinstance(role, list) else role
        user = User(email=email, full_name=claims.get("name", email), role=role if role in ROLES else "viewer")
        db.add(user)
        await db.commit()
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown or inactive user")
    return user


def require_roles(*roles: str):
    async def dep(user: User = Depends(current_user)) -> User:
        if user.role not in roles and user.role != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires role: {', '.join(roles)}")
        return user

    return dep


def in_scope(user, oem_code: str, region_code: str) -> bool:
    """Row-level scope for sales reps (empty/None scope = unrestricted)."""
    if user.role != "sales_rep":
        return True
    if user.scope_oems and oem_code not in user.scope_oems:
        return False
    if user.scope_regions and region_code not in user.scope_regions:
        return False
    return True
