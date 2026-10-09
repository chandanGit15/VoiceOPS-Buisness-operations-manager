"""Approvals + audit endpoints (Part 6): the human-in-the-loop surface."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.database.session import get_session
from app.models import User
from app.services import approvals
from app.services.approvals import ApprovalError

router = APIRouter(tags=["approvals"])


class ApprovalOut(BaseModel):
    id: str
    action: str
    tool_name: str
    payload: dict
    risk_level: str
    status: str
    summary: str
    created_at: datetime | None = None
    resolved_at: datetime | None = None


class ApprovalResolveIn(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")
    confirmed: bool = Field(default=False, description="Explicit confirmation; required for high-risk actions")
    edited_payload: dict | None = Field(default=None, description="Optional edits applied before execution")


class AuditOut(BaseModel):
    id: str
    event: str
    tool_name: str | None = None
    detail: dict
    created_at: datetime | None = None


@router.get("/approvals", response_model=list[ApprovalOut], tags=["approvals"])
async def get_approvals(
    status_filter: str | None = Query(default=None, alias="status",
                                      pattern="^(pending|approved|rejected|executed|failed)$"),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> list[ApprovalOut]:
    rows = await approvals.list_approvals(session, user.id, status_filter)
    return [_approval_out(a) for a in rows]


@router.post("/approvals/{approval_id}/resolve", response_model=dict, tags=["approvals"])
async def resolve_approval(approval_id: uuid.UUID, payload: ApprovalResolveIn,
                           session: AsyncSession = Depends(get_session),
                           user: User = Depends(get_current_user)) -> dict:
    """Approve (executes the real tool), reject, or approve-with-edits.

    Approved high-risk actions additionally require `confirmed: true`.
    """
    try:
        return await approvals.resolve_approval(
            session, user, approval_id,
            decision=payload.decision,
            confirmed=payload.confirmed,
            edited_payload=payload.edited_payload,
        )
    except ApprovalError as exc:
        raise HTTPException(status_code=exc.status_code, detail={
            "code": exc.code, "message": exc.message,
        }) from exc


@router.get("/audit", response_model=list[AuditOut], tags=["approvals"])
async def get_audit(limit: int = Query(default=100, ge=1, le=500),
                    session: AsyncSession = Depends(get_session),
                    user: User = Depends(get_current_user)) -> list[AuditOut]:
    rows = await approvals.list_audit(session, user.id, limit)
    return [
        AuditOut(id=str(row.id), event=row.event, tool_name=row.tool_name,
                 detail=row.detail, created_at=row.created_at)
        for row in rows
    ]


def _approval_out(a) -> ApprovalOut:
    return ApprovalOut(
        id=str(a.id), action=a.action, tool_name=a.tool_name, payload=a.payload,
        risk_level=a.risk_level, status=a.status, summary=a.summary,
        created_at=a.created_at, resolved_at=a.resolved_at,
    )
