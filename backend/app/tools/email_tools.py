"""Email Agent tools (real Gmail API via OAuth)."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, EmailStr, Field  # noqa: F401

from app.tools.google_base import ApprovalGateTool, GoogleTool


class SearchEmailsArgs(BaseModel):
    query: str = Field(min_length=1, max_length=300, description="Gmail search query (e.g. 'from:rahul meeting')")
    max_results: int = Field(default=10, ge=1, le=25)


class SearchEmailsTool(GoogleTool):
    name = "search_emails"
    description = "Search the user's Gmail (read-only). Query uses Gmail search syntax."
    agent = "email"

    class Args(SearchEmailsArgs):
        pass

    async def _run(self, args: Args) -> dict:
        messages = await self.gmail.search_emails(query=args.query, max_results=args.max_results)
        return {"count": len(messages), "messages": messages}


class ReadEmailArgs(BaseModel):
    message_id: str = Field(min_length=1, max_length=128)


class ReadEmailTool(GoogleTool):
    name = "read_email"
    description = "Read one email by its Gmail message id (read-only)."
    agent = "email"

    class Args(ReadEmailArgs):
        pass

    async def _run(self, args: Args) -> dict:
        return await self.gmail.read_email(args.message_id)


class DraftEmailArgs(BaseModel):
    to: str = Field(min_length=3, max_length=320)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=8000)


class DraftEmailTool(GoogleTool):
    """Drafts are low-risk: they are created in Gmail but nothing is sent."""

    name = "draft_email"
    description = "Create an email draft in Gmail without sending it."
    agent = "email"
    action = "email.draft_email"
    risk_level = "low"

    class Args(DraftEmailArgs):
        pass

    async def _run(self, args: Args) -> dict:
        # Draft creation executes directly (nothing leaves the account),
        # but we still record an audit trail via a low-risk approval row.
        result = await self.gmail.create_draft(to=args.to, subject=args.subject, body=args.body)
        return {**result, "note": "Draft created in Gmail. Nothing was sent."}


class SendEmailArgs(BaseModel):
    to: str = Field(min_length=3, max_length=320)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=8000)
    draft_id: Optional[str] = Field(default=None, max_length=128)


class SendEmailTool(ApprovalGateTool):
    """Sending email is the canonical human-in-the-loop action.

    Agent call → pending approval → user approves in the panel → real send.
    High risk: always requires explicit confirmation, even for approvals
    submitted by API.
    """

    name = "send_email"
    description = "Send an email (requires explicit human approval; HIGH RISK)."
    agent = "email"
    action = "email.send_email"
    risk_level = "high"

    class Args(SendEmailArgs):
        pass

    def _payload_summary(self, args: Args) -> str:
        return f"Send email to {args.to}: “{args.subject}”"

    async def _execute(self, args: Args) -> dict:
        return await self.gmail.send_email(
            to=args.to, subject=args.subject, body=args.body, draft_id=args.draft_id
        )
