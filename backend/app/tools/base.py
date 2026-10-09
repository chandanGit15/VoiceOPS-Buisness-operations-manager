"""
Tool abstraction.

Every tool is a real network/data operation — never simulated. The base
class centralizes three concerns so concrete tools stay tiny:

1. Argument validation (strict Pydantic schemas; agents never pass raw dicts).
2. Retries with backoff for transient failures (network, rate limits).
3. Execution trace events (safe metadata only: names, status, timing).
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ValidationError

from app.core.config import get_settings

logger = logging.getLogger("app.tools")


class ToolError(Exception):
    """Raised by concrete tools when the operation fails."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class TraceEvent:
    """One safe execution event (no prompts, no raw tool payloads)."""

    def __init__(
        self,
        *,
        agent: str,
        action: str,
        tool: str | None = None,
        status: str = "ok",
        error: str | None = None,
        duration_ms: float = 0.0,
    ) -> None:
        self.agent = agent
        self.action = action
        self.tool = tool
        self.status = status
        self.error = error
        self.duration_ms = round(duration_ms, 1)
        self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "action": self.action,
            "tool": self.tool,
            "status": self.status,
            "timestamp": self.timestamp,
            "duration_ms": self.duration_ms,
            "error": self.error,
        }


class ToolTrace:
    """Accumulates trace events for one orchestration run."""

    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    def add(self, event: TraceEvent) -> TraceEvent:
        self.events.append(event)
        return event

    def add_event(
        self,
        *,
        agent: str,
        action: str,
        tool: str | None = None,
        status: str = "ok",
        error: str | None = None,
        duration_ms: float = 0.0,
    ) -> dict[str, Any]:
        """Convenience builder matching the safe-metadata event shape."""
        event = TraceEvent(
            agent=agent, action=action, tool=tool, status=status,
            error=error, duration_ms=duration_ms,
        )
        self.events.append(event.to_dict())
        return event.to_dict()

    def to_list(self) -> list[dict[str, Any]]:
        return [e.to_dict() if isinstance(e, TraceEvent) else e for e in self.events]


class Tool(ABC):
    """Base class for all executable tools."""

    # Overridden by subclasses
    name: str = "tool"
    description: str = ""
    agent: str = "supervisor"

    class Args(BaseModel):
        """Argument schema; subclasses replace this."""

    def __init__(self, trace: ToolTrace | None = None) -> None:
        self.trace = trace if trace is not None else ToolTrace()
        # Execution context (DB session/user/direct_execution), injected by
        # the graph or the approval resolver. Always a dict, never None.
        self.ctx: dict = {}

    @abstractmethod
    async def _run(self, args: BaseModel) -> Any:
        """Perform the real operation. Raise ToolError on failure."""

    async def run(self, **kwargs: Any) -> Any:
        """Validate args, execute with retries, and record a trace event."""
        started = time.perf_counter()
        try:
            args = self.Args(**kwargs)
        except ValidationError as exc:
            self.trace.add_event(
                agent=self.agent, action="tool_call", tool=self.name,
                status="error", error="invalid arguments", duration_ms=0.0,
            )
            raise ToolError(f"Invalid arguments for {self.name}: {exc.errors()[:3]}") from exc

        settings = get_settings()
        last_error: ToolError | None = None
        for attempt in range(settings.tool_max_retries + 1):
            try:
                result = await self._run(args)
                duration = (time.perf_counter() - started) * 1000
                self.trace.add_event(
                    agent=self.agent, action="tool_call", tool=self.name,
                    status="ok", duration_ms=duration,
                )
                return result
            except ToolError as exc:
                last_error = exc
                if not exc.retryable or attempt == settings.tool_max_retries:
                    break
                logger.warning(
                    "tool %s failed (attempt %d/%d): %s — retrying",
                    self.name, attempt + 1, settings.tool_max_retries + 1, exc,
                )
                await asyncio.sleep(settings.tool_retry_delay_seconds * (attempt + 1))
            except Exception as exc:  # unexpected: never retry, surface as tool error
                logger.exception("unexpected error in tool %s", self.name)
                last_error = ToolError(f"unexpected {type(exc).__name__}")
                break

        duration = (time.perf_counter() - started) * 1000
        self.trace.add_event(
            agent=self.agent, action="tool_call", tool=self.name,
            status="error",
            error=str(last_error) if last_error else "unknown error",
            duration_ms=duration,
        )
        raise last_error or ToolError("tool failed")
