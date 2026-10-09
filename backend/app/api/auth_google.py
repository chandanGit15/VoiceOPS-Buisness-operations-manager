"""Google OAuth endpoints (Part 6).

The browser only ever receives redirects and JSON status — client secrets
and tokens never leave the backend. The OAuth `state` parameter is stored
in-memory per login attempt and validated on callback (CSRF protection).
"""

import logging
import time

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import get_current_user
from app.database.session import get_session
from app.models import User
from app.services import google_oauth
from app.services.google_oauth import OAuthError

logger = logging.getLogger("app.api.auth_google")

router = APIRouter(prefix="/auth/google", tags=["auth"])

# Single-process dev: in-memory state store (Redis-backed multi-worker in later part).
_pending_states: dict[str, float] = {}
STATE_TTL_SECONDS = 600


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    from app.core.errors import error_payload

    return JSONResponse(status_code=status_code, content=error_payload(code, message))


@router.get("/login", summary="Start Google OAuth (redirects to Google)", response_class=RedirectResponse)
async def login() -> RedirectResponse:
    try:
        url, state = google_oauth.build_authorization_url()
    except OAuthError as exc:
        raise exc
    _pending_states[state] = time.time()
    return RedirectResponse(url=url, status_code=302)


@router.get("/callback", summary="Google OAuth callback (redirects to the dashboard)")
async def callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> RedirectResponse:
    # Dashboard URL to return to. First allowed CORS origin (the frontend
    # itself); remains configurable via CORS_ORIGINS in production.
    settings = get_settings()
    origins = settings.cors_origins_list
    frontend = origins[0] if origins else "http://127.0.0.1:3000"

    if error:
        return RedirectResponse(f"{frontend}/#google_auth_error", status_code=302)
    if not code or not state or state not in _pending_states:
        return RedirectResponse(f"{frontend}/#google_auth_invalid_state", status_code=302)
    if time.time() - _pending_states.pop(state) > STATE_TTL_SECONDS:
        return RedirectResponse(f"{frontend}/#google_auth_state_expired", status_code=302)

    try:
        token_response = await google_oauth.exchange_code_for_tokens(code)
        await google_oauth.store_tokens(session, user, token_response)
    except OAuthError as exc:
        logger.warning("OAuth callback failed: %s", exc.message)
        return RedirectResponse(f"{frontend}/#google_auth_failed", status_code=302)

    return RedirectResponse(f"{frontend}/#google_auth_success", status_code=302)


@router.get("/status", summary="Google connection status for the current user")
async def status(session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)) -> dict:
    connected = await google_oauth.is_connected(session, user)
    settings = get_settings()
    return {
        "connected": connected,
        "configured": bool(settings.google_client_id and settings.google_client_secret),
    }


@router.post("/disconnect", summary="Disconnect the stored Google credentials")
async def disconnect(session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)) -> dict:
    await google_oauth.disconnect(session, user)
    return {"disconnected": True}
