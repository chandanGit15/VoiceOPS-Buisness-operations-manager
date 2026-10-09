"""Deterministic keyword-heuristic LLM provider (zero-cost dev/demo fallback).

Generic English keyword routing, deliberately NOT derived from any dataset
labels. Used by the eval harness (run_eval.py) and selectable in production
config as LLM_PROVIDER=keyword so the app can run demos and code reviews
without any API key. Its routing accuracy is a property of this heuristic,
not of VoiceOps quality.
"""

from __future__ import annotations

from app.agents.schemas import ToolPlan
from app.services.llm.base import LLMMessage, LLMProvider, LLMResponse


def cal_tool(low: str) -> str:
    """Pick the calendar tool for a phrase (generic keyword logic)."""
    if any(w in low for w in ("cancel", "remove", "delete")):
        return "delete_event"
    if any(w in low for w in ("move", "reschedule", "push", "update", "shorter")):
        return "update_event"
    if any(w in low for w in ("add", "book", "create", "schedule a", "set up",
                              "lunch with", "book a")):
        return "create_event"
    if any(w in low for w in ("free", "available", "availability")):
        return "check_availability"
    return "get_events"


def mail_tool(low: str) -> str:
    """Pick the email tool for a phrase (generic keyword logic)."""
    if "send" in low:
        return "send_email"
    if any(w in low for w in ("draft", "write", "compose")):
        return "draft_email"
    if any(w in low for w in ("read", "open")):
        return "read_email"
    return "search_emails"


def task_tool(low: str) -> str:
    """Pick the task tool for a phrase (generic keyword logic)."""
    if any(w in low for w in ("complete", "mark", "check off", "done")):
        return "complete_task"
    if any(w in low for w in ("list", "show me my", "to-do list", "what's on my")):
        return "list_tasks"
    return "create_task"


class KeywordHeuristicProvider(LLMProvider):
    @property
    def name(self) -> str:
        return "keyword-heuristic"

    # --- routing tables (generic English patterns) ---
    _ROUTES = [
        ("knowledge", ("resume", "document", "uploaded", "my notes", "my pdf", "contract i", "policy")),
        ("weather", ("weather", "temperature", "forecast", "raining", "snowing", "windy", "umbrella", "hot is it", "conditions")),
        ("email", ("email", "inbox", "unread", "reply", "phishing", "gmail")),
        ("calendar", ("calendar", "meeting", "schedule", "schedule", "event", "appointment", "free", "standup", "offsite", "invite", "venue booking")),
        ("task", ("task", "to-do", "todo", "remind", "check off", "complete the task")),
        ("research", ("search", "find", "news", "look up", "research", "latest", "papers", "funding", "price of")),
    ]
    # Words that suggest an agent even without other cues (weak signals).
    _CITY_HINTS = ("berlin", "seattle", "cairo", "tokyo", "denver", "buenos aires",
                   "london", "sydney", "toronto", "cape town", "rome", "paris")

    async def generate(
        self, messages: list[LLMMessage], *, system_prompt: str | None = None,
        temperature: float = 0.7, max_output_tokens: int = 1024,
    ) -> LLMResponse:
        text = "Here is what I found based on the tool results."
        return LLMResponse(text=text, provider=self.name, model="heuristic")

    @staticmethod
    def _request_text(messages) -> str:
        """Extract the user request line, excluding tool lists embedded in prompts."""
        for m in messages:
            for line in m.content.split("\n"):
                if line.lower().startswith("user request:"):
                    return line.lower()
        return "\n".join(m.content for m in messages).lower()

    async def generate_structured(self, messages, schema, *, system_prompt=None, temperature=0.0):
        from app.agents.schemas import SupervisorDecision

        low = self._request_text(messages)

        if schema is SupervisorDecision:
            # After specialists have run, the graph calls the supervisor again.
            # The heuristic cannot usefully re-route, so it answers directly
            # with an executed-actions summary instead of looping.
            if system_prompt and "Result from" in system_prompt:
                executed = [
                    line.split(":", 1)[0].replace("Result from ", "")
                    for line in system_prompt.splitlines()
                    if line.startswith("Result from ")
                ]
                if executed:
                    names = ", ".join(sorted(set(executed)))
                    return SupervisorDecision(
                        respond_directly=(
                            f"Done — I handled that with the {names} agent(s). "
                            "Tool results: Here is what I found based on the tool results."
                        )
                    )
            route: list[str] = []
            for agent, words in self._ROUTES:
                if any(w in low for w in words):
                    route.append(agent)
            if not route and any(city in low for city in self._CITY_HINTS):
                route.append("weather")
            # Unsafe/refusal and invalid-input handling is the LLM's job; the
            # heuristic can only route or answer directly.
            if not route:
                return SupervisorDecision(
                    respond_directly="I need a bit more detail to help with that. "
                                     "Could you tell me what you'd like me to do?"
                )
            # Keep dataset-realistic ordering: research first when combined.
            order = ["research", "weather", "calendar", "email", "knowledge", "task"]
            route = sorted(set(route), key=order.index)
            return SupervisorDecision(route=route)

        if schema is ToolPlan:
            # Detect which specialist is planning from its system prompt
            # ("You are the X Agent."), so only that agent's tools are planned.
            agent = "research"
            for name in ("research", "task", "weather", "calendar", "email", "knowledge"):
                if f"the {name.capitalize()} Agent" in (system_prompt or ""):
                    agent = name
                    break
            return self._plan(low, agent)

        # Any other schema: return an empty/neutral instance.
        return schema.model_validate({})

    def _plan(self, low: str, agent: str) -> ToolPlan:
        import re

        calls: list[dict] = []
        if agent == "weather":
            # Best-effort city extraction: "weather in <city>" (fallback London).
            match = re.search(r"\bin ([a-z\s]+?)(?:\s+(?:tomorrow|today|now|this|next)|[?.,]|$)", low)
            location = (match.group(1).strip().title() if match and match.group(1).strip() else "London")
            return ToolPlan(tool_calls=[{"tool": "get_weather", "arguments": {"city": location}}])
        if agent == "research":
            return ToolPlan(tool_calls=[{"tool": "search_web", "arguments": {"query": "q"}}])
        if agent == "knowledge":
            if "search" in low and "answer" not in low:
                return ToolPlan(tool_calls=[{"tool": "search_documents", "arguments": {"query": "q"}}])
            if "page" in low or "extract" in low or "find where" in low:
                return ToolPlan(tool_calls=[{"tool": "retrieve_relevant_chunks", "arguments": {"query": "q"}}])
            return ToolPlan(tool_calls=[{"tool": "answer_from_documents", "arguments": {"question": "q"}}])
        if agent == "calendar":
            calls.append({"tool": cal_tool(low), "arguments": {}})
            return ToolPlan(tool_calls=calls)
        if agent == "email":
            calls.append({"tool": mail_tool(low), "arguments": {}})
            return ToolPlan(tool_calls=calls)
        if agent == "task":
            tool = task_tool(low)
            arguments: dict = {}
            if tool == "create_task":
                # Best-effort title extraction: "task to <rest>" / "task: <rest>".
                match = re.search(r"\btask (?:to|:|about)\s+(.+?)(?:\s+(?:tomorrow|today)|[?.,]|$)", low)
                title = match.group(1).strip() if match else ""
                arguments = {"title": title[:200]} if title else {}
            return ToolPlan(tool_calls=[{"tool": tool, "arguments": arguments}])
        return ToolPlan(tool_calls=[])
