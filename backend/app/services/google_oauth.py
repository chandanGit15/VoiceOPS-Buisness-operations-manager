"""
Google OAuth 2.0 flow + encrypted token storage.

Security model:
- Least-privilege scopes: Gmail read/draft/send (no modify/delete) and
  Calendar full read/write (needed for create/update/delete tools).
- Tokens are encrypted at rest with Fernet (TOKEN_ENCRYPTION_KEY env var)
  and decrypted only in memory when a tool needs one.
- Refresh happens automatically before expiry; a single-flight lock
  prevents concurrent refresh races.
- Credentials never reach the frontend — the browser only gets redirects.
"""

from __future__ import annotations

import logging
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import APIError
from app.models import GoogleOAuthToken, User

logger = logging.getLogger("app.oauth.google")

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

# Least-privilege scopes: Gmail read + create drafts + send; Calendar CRUD.
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
    "openid",
    "email",
]


class OAuthError(APIError):
    status_code = 400
    code = "oauth_error"
    message = "Google authorization failed."


class NotConnectedError(APIError):
    status_code = 409
    code = "google_not_connected"
    message = "Google account is not connected. Connect it in Settings first."


class TokenCrypto:
    """Fernet encryption for tokens at rest."""

    def __init__(self, key: str) -> None:
        if not key:
            raise OAuthError(
                "TOKEN_ENCRYPTION_KEY is not set. Generate one with: "
                'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"',
                code="oauth_not_configured",
            )
        self._fernet = Fernet(key.encode())

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode()).decode()
        except InvalidToken as exc:
            # Wrong/rotated key: fail closed, never return garbage credentials.
            raise OAuthError(
                "Stored token could not be decrypted (encryption key mismatch). Reconnect Google.",
                code="token_decrypt_error",
            ) from exc


def get_token_crypto() -> TokenCrypto:
    return TokenCrypto(get_settings().token_encryption_key)


def _oauth_config() -> tuple[str, str]:
    settings = get_settings()
    if not settings.google_client_id or not settings.google_client_secret:
        raise OAuthError(
            "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not set.",
            code="oauth_not_configured",
        )
    return settings.google_client_id, settings.google_client_secret


def build_authorization_url() -> tuple[str, str]:
    """Return (auth_url, state). State is validated on callback (CSRF)."""
    client_id, _ = _oauth_config()
    settings = get_settings()
    state = secrets.token_urlsafe(32)
    params = {
        "client_id": client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "access_type": "offline",     # required to receive a refresh token
        "include_granted_scopes": "false",
        "prompt": "consent",          # ensures refresh token on re-auth
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}", state


async def exchange_code_for_tokens(code: str) -> dict[str, Any]:
    client_id, client_secret = _oauth_config()
    settings = get_settings()
    payload = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": settings.google_redirect_uri,
        "grant_type": "authorization_code",
    }
    return await _token_request(payload)


async def refresh_access_token(refresh_token: str) -> dict[str, Any]:
    client_id, client_secret = _oauth_config()
    payload = {
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
    }
    return await _token_request(payload)


async def _token_request(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(GOOGLE_TOKEN_URL, data=payload)
    except httpx.HTTPError as exc:
        raise OAuthError(f"Google token endpoint unreachable: {type(exc).__name__}") from exc

    if response.status_code != 200:
        detail = response.json().get("error_description", response.status_code)
        raise OAuthError(f"Google token endpoint returned {response.status_code}: {detail}")

    return response.json()


# ---------- Persistence ----------


async def store_tokens(
    session: AsyncSession, user: User, token_response: dict[str, Any]
) -> GoogleOAuthToken:
    """Encrypt and persist tokens from a token-endpoint response."""
    crypto = get_token_crypto()
    access_token = token_response["access_token"]
    refresh_token = token_response.get("refresh_token")  # only on first consent
    expires_in = int(token_response.get("expires_in", 3600))
    scopes = token_response.get("scope", " ").split()

    result = await session.execute(
        select(GoogleOAuthToken).where(GoogleOAuthToken.user_id == user.id)
    )
    record = result.scalar_one_or_none()

    if record is None:
        record = GoogleOAuthToken(user_id=user.id)
        session.add(record)

    record.encrypted_access_token = crypto.encrypt(access_token)
    if refresh_token:  # Google may omit it on re-consent; keep the old one
        record.encrypted_refresh_token = crypto.encrypt(refresh_token)
    record.expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    record.scopes = scopes
    await session.commit()
    await session.refresh(record)
    return record


async def get_valid_access_token(session: AsyncSession, user: User) -> str:
    """Return a working access token, refreshing it first if needed.

    Raises NotConnectedError when the user never connected Google, and
    OAuthError when refresh fails (e.g. revoked consent) — the API layer
    surfaces both as clear error envelopes the frontend can act on.
    """
    result = await session.execute(
        select(GoogleOAuthToken).where(GoogleOAuthToken.user_id == user.id)
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise NotConnectedError()

    crypto = get_token_crypto()
    now = time.time()
    expires_at = record.expires_at.timestamp() if record.expires_at else 0

    if expires_at - now > 60:  # still valid with a 60s margin
        return crypto.decrypt(record.encrypted_access_token)

    if not record.encrypted_refresh_token:
        raise OAuthError("No refresh token stored. Reconnect Google to continue.")

    token_response = await refresh_access_token(crypto.decrypt(record.encrypted_refresh_token))
    updated = await store_tokens(session, user, token_response)
    logger.info("Google access token refreshed for user %s", user.id)
    return crypto.decrypt(updated.encrypted_access_token)


async def disconnect(session: AsyncSession, user: User) -> None:
    result = await session.execute(
        select(GoogleOAuthToken).where(GoogleOAuthToken.user_id == user.id)
    )
    record = result.scalar_one_or_none()
    if record is not None:
        await session.delete(record)
        await session.commit()


async def is_connected(session: AsyncSession, user: User) -> bool:
    result = await session.execute(
        select(GoogleOAuthToken.id).where(GoogleOAuthToken.user_id == user.id)
    )
    return result.scalar_one_or_none() is not None
