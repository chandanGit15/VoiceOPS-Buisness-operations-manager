"""Health check endpoints for the API v1 surface.

Two probes, cloud-friendly:

- GET /health        — liveness/readiness. Fast, unauthenticated, no
  external calls: safe for Cloud Run startup/liveness probes and load
  balancer health checks. A failing database must NOT fail this probe
  (the instance may still be starting; Cloud Run restart policy handles
  crash loops).
- GET /health/detail — readiness diagnostics. Reports database reachability
  and migration revision. Authenticated? No — but it exposes only booleans
  and a revision string, never credentials. Cloud consoles / uptime checks
  may poll it; it performs one cheap DB query with a hard timeout.
"""

import asyncio
import time

from fastapi import APIRouter
from sqlalchemy import text

from app.database.session import get_engine
from app.schemas.health import HealthDetailResponse, HealthResponse

router = APIRouter()

_DB_CHECK_TIMEOUT_SECONDS = 3.0


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness probe (fast, no external dependencies)",
    tags=["health"],
)
async def health() -> HealthResponse:
    """Cloud health check: process is up and can serve requests.

    Deliberately does NOT touch the database or external APIs — a slow or
    unavailable database would otherwise flap the probe and cause restart
    storms during deploys.
    """
    return HealthResponse(status="ok", service="voiceops")


@router.get(
    "/health/detail",
    response_model=HealthDetailResponse,
    summary="Readiness diagnostics (database reachability + migration revision)",
    tags=["health"],
)
async def health_detail() -> HealthDetailResponse:
    """Detailed readiness: one bounded DB query; never raises to the caller."""
    db_ok = False
    current_revision: str | None = None
    started = time.perf_counter()
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            current_revision = (await asyncio.wait_for(
                conn.execute(text("SELECT version_num FROM alembic_version")),
                timeout=_DB_CHECK_TIMEOUT_SECONDS,
            )).scalar_one_or_none()
            db_ok = current_revision is not None
    except Exception:
        db_ok = False  # detail probe reports; it never fails the process
    latency_ms = round((time.perf_counter() - started) * 1000, 1)

    return HealthDetailResponse(
        status="ok" if db_ok else "degraded",
        service="voiceops",
        database={"reachable": db_ok, "migration_revision": current_revision},
        check_latency_ms=latency_ms,
    )
