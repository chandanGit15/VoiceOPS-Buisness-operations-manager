"""
Base classes for Google tools (Calendar + Email).

Two execution models:
- Read-only tools (get_events, check_availability, search_emails, read_email)
  execute immediately — they only need Google connectivity.
- Mutating tools go through human approval: run() records a pending Approval
  and returns an "awaiting approval" marker instead of executing. The real
  action runs only after the user approves it in the Approvals panel
  (approvals.resolve_approval → tool.execute()).
"""

from __future__ import annotations

from typing import Any

from app.services import approvals as approvals_service
from app.services.google_clients import CalendarClient, GmailClient, GoogleContext
from app.tools.base import Tool, ToolError


class GoogleTool(Tool):
    """Shared plumbing: lazy Google clients from the injected request context."""

    # Set by the API layer / approval executor (session + current user).
    ctx: dict[str, Any] | None = None

    def _context(self) -> GoogleContext:
        if not self.ctx or "session" not in self.ctx:
            raise ToolError(
                "Google account is not connected or session context is missing. "
                "Connect Google in Settings first."
            )
        return GoogleContext(session=self.ctx["session"], user=self.ctx["user"])

    @property
    def calendar(self) -> CalendarClient:
        return CalendarClient(self._context())

    @property
    def gmail(self) -> GmailClient:
        return GmailClient(self._context())


class ApprovalGateTool(GoogleTool):
    """A mutating tool that requires explicit human approval before executing."""

    action: str = ""            # e.g. "email.send_email"
    risk_level: str = "medium"  # low | medium | high

    def _payload_summary(self, args) -> str:  # pragma: no cover - overridden
        return ""

    async def _execute(self, args) -> Any:  # pragma: no cover - overridden
        raise NotImplementedError

    async def _run(self, args) -> dict:
        """Record a pending approval instead of executing (fail-safe default)."""
        if self.ctx and self.ctx.get("direct_execution"):
            # Path used by the approval resolver after the human said yes.
            return await self._execute(args)

        session = self.ctx["session"] if self.ctx else None
        user = self.ctx.get("user") if self.ctx else None
        if session is None or user is None:
            raise ToolError("Google account is not connected. Connect it in Settings first.")

        payload = args.model_dump(mode="json", exclude_none=True)
        approval = await approvals_service.create_approval(
            session,
            user=user,
            action=self.action,
            tool_name=self.name,
            payload=payload,
            risk_level=self.risk_level,
            summary=self._payload_summary(args),
        )
        return {
            "awaiting_approval": True,
            "approval_id": str(approval.id),
            "risk_level": self.risk_level,
            "summary": approval.summary,
            "message": "This action needs your approval. Open the Approvals panel to confirm.",
        }
