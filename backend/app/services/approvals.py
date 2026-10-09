"""
Human-in-the-loop approval service.

Sensitive actions never execute directly from an agent: the tool records an
Approval row (pending) and returns "awaiting approval". The user resolves it
from the Approvals panel; only then is the real tool executed and the audit
trail updated. High-risk actions additionally require an explicit
confirmation flag from the frontend.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import APIError
from app.models import Approval, AuditLog, User

logger = logging.getLogger("app.approvals")


class ApprovalError(APIError):
    status_code = 409
    code = "approval_error"
    message = "Approval could not be processed."


async def record_audit(
    session: AsyncSession, *, user_id, event: str, tool_name: str | None = None, detail: dict | None = None
) -> None:
    session.add(AuditLog(user_id=user_id, event=event, tool_name=tool_name, detail=detail or {}))
    await session.flush()


async def create_approval(
    session: AsyncSession,
    *,
    user: User,
    action: str,
    tool_name: str,
    payload: dict[str, Any],
    risk_level: str,
    summary: str,
) -> Approval:
    approval = Approval(
        user_id=user.id,
        action=action,
        tool_name=tool_name,
        payload=payload,
        risk_level=risk_level,
        summary=summary[:500],
    )
    session.add(approval)
    await record_audit(
        session, user_id=user.id, event="approval.created",
        tool_name=tool_name, detail={"risk_level": risk_level, "action": action},
    )
    await session.commit()
    await session.refresh(approval)
    return approval


async def list_approvals(session: AsyncSession, user_id=None, status: str | None = None, limit: int = 50) -> list[Approval]:
    query = select(Approval).order_by(desc(Approval.created_at)).limit(limit)
    if user_id is not None:
        query = query.where(Approval.user_id == user_id)
    if status:
        query = query.where(Approval.status == status)
    result = await session.execute(query)
    return list(result.scalars())


async def get_approval(session: AsyncSession, approval_id, user_id=None) -> Approval | None:
    result = await session.execute(select(Approval).where(Approval.id == approval_id))
    approval = result.scalar_one_or_none()
    if approval is None:
        return None
    if user_id is not None and approval.user_id is not None and approval.user_id != user_id:
        return None  # ownership check: foreign approvals look missing
    return approval


async def resolve_approval(
    session: AsyncSession,
    user: User,
    approval_id,
    *,
    decision: str,          # "approved" | "rejected"
    confirmed: bool = False,
    edited_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve a pending approval; execute the real tool when approved."""
    approval = await get_approval(session, approval_id, user_id=user.id)
    # Foreign approvals are indistinguishable from missing ones (404 semantics).
    if approval is None:
        raise ApprovalError("Approval not found.", code="approval_not_found")
    if approval.status != "pending":
        raise ApprovalError(f"Approval already {approval.status}.", code="approval_already_resolved")

    now = datetime.now(timezone.utc)

    if decision == "rejected":
        approval.status = "rejected"
        approval.resolved_at = now
        await record_audit(session, user_id=user.id, event="approval.rejected",
                           tool_name=approval.tool_name, detail={"summary": approval.summary})
        await session.commit()
        return {"status": "rejected", "executed": False}

    if approval.risk_level == "high" and not confirmed:
        raise ApprovalError(
            "High-risk actions require explicit confirmation.",
            code="confirmation_required",
        )

    payload = edited_payload if edited_payload is not None else approval.payload
    approval.status = "approved"
    approval.resolved_at = now
    await record_audit(session, user_id=user.id, event="approval.approved",
                       tool_name=approval.tool_name,
                       detail={"edited": edited_payload is not None})

    # Execute the real tool now (this is the only execution path).
    # Lazy import: app.tools.registry imports approvals transitively, so a
    # module-level import here would be circular.
    from app.tools.registry import tools_for

    agent_name = approval.action.split(".")[0] if "." in approval.action else "supervisor"
    tool_classes = tools_for(agent_name)
    tool_class = tool_classes.get(approval.tool_name)
    if tool_class is None:
        approval.status = "failed"
        await record_audit(session, user_id=user.id, event="approval.execution_failed",
                           tool_name=approval.tool_name, detail={"error": "tool not registered"})
        await session.commit()
        raise ApprovalError("Tool for this approval is no longer available.")

    tool = tool_class(trace=None)
    # direct_execution bypasses the approval gate — the human already said yes.
    tool.ctx = {
        "session": session,
        "user": user,
        "direct_execution": True,
    }

    try:
        result = await tool.run(**payload)
        approval.status = "executed"
        await record_audit(session, user_id=user.id, event="approval.executed",
                           tool_name=approval.tool_name, detail={"result_keys": list(result) if isinstance(result, dict) else []})
        outcome = {"status": "executed", "executed": True, "result": result}
    except Exception as exc:
        approval.status = "failed"
        await record_audit(session, user_id=user.id, event="approval.execution_failed",
                           tool_name=approval.tool_name, detail={"error": str(exc)[:300]})
        logger.exception("approved action failed: %s", approval.tool_name)
        outcome = {"status": "failed", "executed": False, "error": str(exc)[:300]}

    await session.commit()
    return outcome


async def list_audit(session: AsyncSession, user_id=None, limit: int = 100) -> list[AuditLog]:
    query = select(AuditLog).order_by(desc(AuditLog.created_at)).limit(limit)
    if user_id is not None:
        query = query.where(AuditLog.user_id == user_id)
    result = await session.execute(query)
    return list(result.scalars())
