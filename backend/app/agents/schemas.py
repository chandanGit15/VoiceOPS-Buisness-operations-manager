"""Structured-output schemas for agent routing and tool planning.

These are the ONLY shapes agents may emit — no free-form chain-of-thought
is ever stored or returned to clients.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

AgentName = Literal["research", "task", "weather", "calendar", "email", "knowledge"]


class SupervisorDecision(BaseModel):
    """Supervisor routing decision (one per supervisor turn)."""

    route: list[AgentName] = Field(
        default_factory=list,
        description="Agents to delegate to this step, in execution order.",
    )
    respond_directly: str = Field(
        default="",
        description="Final reply text when no delegation is needed.",
    )


class ToolCallRequest(BaseModel):
    """One tool invocation requested by a specialist agent."""

    tool: str = Field(min_length=1, max_length=64)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolPlan(BaseModel):
    """A specialist agent's tool plan (may be empty → answer from context)."""

    tool_calls: list[ToolCallRequest] = Field(default_factory=list, max_length=5)
