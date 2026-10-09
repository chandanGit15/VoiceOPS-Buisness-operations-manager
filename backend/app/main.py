"""FastAPI application factory and startup wiring for VoiceOps."""

import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api import approvals, auth_google, auth_users, chat, data, documents, health, voice
from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging

logger = logging.getLogger("app.main")


def _init_error_monitoring() -> None:
    """Optionally attach Sentry when SENTRY_DSN is configured.

    Import is deferred so local development (no DSN) never needs the
    package installed. Only exceptions are captured — no PII, no request
    bodies; the request_id middleware id travels via default tags.
    """
    dsn = get_settings().sentry_dsn
    if not dsn:
        return
    try:
        import sentry_sdk

        sentry_sdk.init(dsn=dsn, send_default_pii=False, traces_sample_rate=0.0)
        logger.info("Sentry error monitoring enabled")
    except ImportError:
        logger.warning("SENTRY_DSN set but sentry-sdk not installed — monitoring disabled")


def create_app() -> FastAPI:
    """Build the FastAPI application (factory pattern simplifies testing)."""
    configure_logging()
    settings = get_settings()
    _init_error_monitoring()

    # Production guardrail (Part 9): refuse to boot with insecure config.
    if settings.app_env == "production":
        settings.validate_production()

    app = FastAPI(
        title="VoiceOps API",
        version="0.1.0",
        description=(
            "Multi-agent voice AI operations platform. "
            "This is the Part 1 foundation: health checks, configuration, "
            "and centralized error handling. Agents, tools, voice, and "
            "persistence arrive in later parts."
        ),
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    # CORS: only origins explicitly listed in settings may call the API.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Versioned API surface: all routers mount under /api/v1.
    app.include_router(health.router, prefix=settings.api_v1_prefix)
    app.include_router(auth_users.router, prefix=settings.api_v1_prefix)
    app.include_router(chat.router, prefix=settings.api_v1_prefix)
    app.include_router(data.router, prefix=settings.api_v1_prefix)
    app.include_router(voice.router, prefix=settings.api_v1_prefix)
    app.include_router(auth_google.router, prefix=settings.api_v1_prefix)
    app.include_router(approvals.router, prefix=settings.api_v1_prefix)
    app.include_router(documents.router, prefix=settings.api_v1_prefix)

    register_error_handlers(app)

    # ---------- Observability: request id + structured access log ----------
    # The id is echoed to clients via X-Request-ID and included in the error
    # envelope, so a user can report it and it can be found in the logs.
    # Logs contain ids, method, path, status, duration — never bodies or keys.

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        started = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - started) * 1000
        response.headers["X-Request-ID"] = request_id
        logging.getLogger("app.access").info(
            "request_id=%s method=%s path=%s status=%s duration_ms=%.0f",
            request_id, request.method, request.url.path,
            response.status_code, duration_ms,
        )
        return response

    return app


app = create_app()
