"""
Authentication core (Part 8).

- Passwords: PBKDF2-HMAC-SHA256, 200k iterations, per-user salt,
  constant-time verification. Hashes are never logged or returned.
- Tokens: compact HS256 JWTs signed with APP_SECRET_KEY; expiry enforced.
- get_current_user: FastAPI dependency that resolves the Bearer token to a
  User row. All data endpoints depend on it — no token, no data.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.database.session import get_session
from app.models import User

_PBKDF2_ITERATIONS = 200_000


# ---------- Passwords ----------


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), _PBKDF2_ITERATIONS
    ).hex()
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored or not stored.startswith("pbkdf2_sha256$"):
        return False
    try:
        _, iterations, salt, digest = stored.split("$")
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), salt.encode(), int(iterations)
        ).hex()
        return hmac.compare_digest(candidate, digest)
    except (ValueError, TypeError):
        return False


# ---------- JWT (compact HS256, no external dependency) ----------


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def create_access_token(user: User) -> str:
    settings = get_settings()
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "iat": now,
        "exp": now + settings.access_token_expire_minutes * 60,
    }
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64url(json.dumps(payload, separators=(",", ":")).encode())
    )
    signature = hmac.new(
        settings.app_secret_key.encode(), signing_input.encode(), hashlib.sha256
    ).digest()
    return signing_input + "." + _b64url(signature)


def _bearer_token(authorization: str | None = Header(default=None)) -> str:
    """Extract the raw token from an 'Authorization: Bearer <token>' header."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "invalid_token", "message": "Missing bearer token."},
            headers={"WWW-Authenticate": "Bearer"},
        )
    return authorization.split(" ", 1)[1].strip()


def decode_access_token(token: str = Depends(_bearer_token)) -> dict:
    """Verify signature + expiry; raises HTTPException 401 on any failure.

    Used as a FastAPI dependency: the token itself is resolved from the
    Authorization header via _bearer_token.
    """
    try:
        signing_input, signature_b64 = token.rsplit(".", 1)
        settings = get_settings()
        expected = hmac.new(
            settings.app_secret_key.encode(), signing_input.encode(), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(expected, _b64url_decode(signature_b64)):
            raise ValueError("bad signature")
        header = json.loads(_b64url_decode(signing_input.split(".")[0]))
        if header.get("alg") != "HS256":
            raise ValueError("bad alg")
        payload = json.loads(_b64url_decode(signing_input.split(".", 1)[1].rsplit(".", 1)[0]))
        if payload.get("exp", 0) < time.time():
            raise ValueError("expired")
        return payload
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "invalid_token", "message": "Invalid or expired token."},
        )


async def get_current_user(
    session: AsyncSession = Depends(get_session),
    token_payload: dict = Depends(decode_access_token),
) -> User:
    """Resolve the Bearer token to a User. Depends on get_session so routers
    share the same session instance."""
    result = await session.execute(
        select(User).where(User.id == uuid.UUID(token_payload["sub"]))
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "invalid_token", "message": "User no longer exists."},
        )
    return user
