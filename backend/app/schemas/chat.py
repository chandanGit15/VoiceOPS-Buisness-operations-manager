"""Pydantic schemas for the chat API."""

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """POST /api/v1/chat request body."""

    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = Field(
        default=None,
        max_length=64,
        description="Existing session id; omit to start a new conversation.",
    )


class TraceEvent(BaseModel):
    """One safe execution event (no prompts or raw payloads)."""

    agent: str
    action: str
    tool: str | None = None
    status: str
    timestamp: str
    duration_ms: float = 0.0
    error: str | None = None


class ChatResponse(BaseModel):
    """POST /api/v1/chat response body."""

    message: str
    session_id: str
    agent: str
    trace: list[TraceEvent] = Field(default_factory=list, max_length=50)
