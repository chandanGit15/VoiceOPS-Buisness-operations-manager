"""Pydantic schemas for the health endpoints."""

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Response shape for GET /api/v1/health."""

    status: str
    service: str


class DatabaseHealth(BaseModel):
    """Database readiness sub-report (booleans only — no credentials)."""

    reachable: bool
    migration_revision: str | None = None


class HealthDetailResponse(BaseModel):
    """Response shape for GET /api/v1/health/detail."""

    status: str
    service: str
    database: DatabaseHealth
    check_latency_ms: float
