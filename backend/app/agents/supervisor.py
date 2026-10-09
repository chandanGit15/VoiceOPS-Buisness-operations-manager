"""
Supervisor Agent — the entry-point orchestrator.

Wraps the LangGraph: it keeps the stable `handle(history, message)`
interface the chat API consumes and returns a structured AgentResult
carrying the final message and the execution trace (safe metadata only).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.llm import LLMMessage, LLMProvider, get_llm_provider
from app.tools.base import ToolTrace


@dataclass
class AgentResult:
    """What the chat API needs from one orchestration run."""

    message: str
    trace: list[dict] = field(default_factory=list)
    agents_used: list[str] = field(default_factory=list)


class SupervisorAgent:
    def __init__(self, provider: LLMProvider | None = None) -> None:
        self._provider = provider  # injectable for tests

    def _get_provider(self) -> LLMProvider:
        if self._provider is None:
            self._provider = get_llm_provider()
        return self._provider

    async def handle(
        self,
        history: list[LLMMessage],
        user_message: str,
        *,
        tool_context: dict | None = None,
    ) -> AgentResult:
        """Run the full multi-agent graph for one user turn.

        tool_context (DB session + user) is injected into Google tools so
        they can resolve OAuth tokens and create approval rows.
        """
        # Import here to avoid a circular import at module load.
        from app.agents.graph import build_graph

        trace = ToolTrace()
        graph = build_graph(self._get_provider(), trace, tool_context=tool_context)

        final_state = await graph.ainvoke({
            "user_message": user_message,
            "history": history,
            "trace": [],
            "tool_results": {},
            "route": [],
            "agents_used": [],
            "final_response": "",
            "step": 0,
        })

        message = final_state.get("final_response") or (
            "I completed the requested actions but could not compose a summary. "
            "Check the Agent Activity panel for details."
        )
        return AgentResult(
            message=message,
            trace=final_state.get("trace", []),
            agents_used=final_state.get("agents_used", []),
        )
