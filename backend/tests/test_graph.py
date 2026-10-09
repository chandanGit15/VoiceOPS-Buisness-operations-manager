"""Tests for the LangGraph orchestration: routing and multi-agent flows."""

import pytest
from pydantic import BaseModel

from app.agents.graph import build_graph
from app.agents.schemas import SupervisorDecision, ToolPlan
from app.services import task_store
from app.tools.base import Tool, ToolTrace
from app.tools.registry import tools_for
from tests.fakes import FakeProvider

pytestmark = pytest.mark.asyncio


class FakeWeatherTool(Tool):
    name = "get_weather"
    description = "fake weather"
    agent = "weather"

    class Args(BaseModel):
        city: str

    async def _run(self, args) -> dict:
        return {"city": args.city, "days": [{"condition": "rain"}]}


async def _run(graph, user_message: str) -> dict:
    return await graph.ainvoke({
        "user_message": user_message,
        "history": [],
        "trace": [],
        "tool_results": {},
        "route": [],
        "agents_used": [],
        "final_response": "",
        "step": 0,
    })


async def test_supervisor_routes_weather_request() -> None:
    provider = FakeProvider(structured_queue=[
        SupervisorDecision(route=["weather"]),
        ToolPlan(tool_calls=[{"tool": "get_weather", "arguments": {"city": "London"}}]),
        SupervisorDecision(respond_directly="Rain expected tomorrow."),
    ])
    trace = ToolTrace()
    graph = build_graph(provider, trace, tool_classes_by_agent={
        "research": {}, "task": {}, "weather": {"get_weather": FakeWeatherTool},
    })

    final = await _run(graph, "What's the weather in London?")

    assert final["final_response"] == "Rain expected tomorrow."
    assert final["agents_used"] == ["weather"]
    tool_events = [e for e in final["trace"] if e["tool"] == "get_weather"]
    assert len(tool_events) == 1 and tool_events[0]["status"] == "ok"
    # Supervisor decided twice: delegate, then respond.
    assert len([c for c in provider.structured_calls if c["schema"] == "SupervisorDecision"]) == 2


async def test_supervisor_answers_directly_without_tools() -> None:
    provider = FakeProvider(text="Just chatting!")
    graph = build_graph(provider, ToolTrace())

    final = await _run(graph, "Hello there")

    assert final["final_response"] == "Just chatting!"
    assert final["agents_used"] == []
    assert not [e for e in final["trace"] if e["tool"]]


async def test_multi_agent_flow_weather_then_task(patched_session_factory, db_session_factory) -> None:
    provider = FakeProvider(structured_queue=[
        SupervisorDecision(route=["weather", "task"]),  # step 1: weather first
        ToolPlan(tool_calls=[{"tool": "get_weather", "arguments": {"city": "London"}}]),
        SupervisorDecision(route=["task"]),             # step 2: now the task
        ToolPlan(tool_calls=[{
            "tool": "create_task",
            "arguments": {"title": "Carry an umbrella", "due_date": "2026-10-09"},
        }]),
        SupervisorDecision(respond_directly="Rain tomorrow — reminder created."),
    ])
    trace = ToolTrace()
    graph = build_graph(provider, trace, tool_classes_by_agent={
        "research": {},
        "task": tools_for("task"),  # REAL task tools against the test DB
        "weather": {"get_weather": FakeWeatherTool},
    })

    final = await _run(graph, "Check tomorrow's rain and remind me to take an umbrella")

    assert final["final_response"] == "Rain tomorrow — reminder created."
    assert final["agents_used"] == ["weather", "task"]

    # The task tool really persisted a row.
    async with db_session_factory() as session:
        tasks = await task_store.list_tasks(session)
    assert len(tasks) == 1
    assert tasks[0].title == "Carry an umbrella"
    assert tasks[0].status == "open"

    # Trace shows both agents' tool calls in order.
    tool_events = [e for e in final["trace"] if e["tool"]]
    assert [e["tool"] for e in tool_events] == ["get_weather", "create_task"]


async def test_specialist_rejects_tool_not_allowed_for_it() -> None:
    provider = FakeProvider(structured_queue=[
        SupervisorDecision(route=["task"]),
        ToolPlan(tool_calls=[{"tool": "get_weather", "arguments": {"city": "London"}}]),
        SupervisorDecision(respond_directly="ok"),
    ])
    graph = build_graph(provider, ToolTrace(), tool_classes_by_agent={
        "research": {},
        "task": {},  # task agent gets NO tools
        "weather": {"get_weather": FakeWeatherTool},
    })

    final = await _run(graph, "sneaky request")

    rejected = [e for e in final["trace"] if e.get("error")]
    assert any("not allowed" in (e["error"] or "") for e in rejected)


async def test_step_budget_prevents_infinite_loop(monkeypatch) -> None:
    monkeypatch.setenv("SUPERVISOR_MAX_STEPS", "3")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        # Supervisor keeps delegating to weather forever — budget must stop it.
        provider = FakeProvider(structured_queue=[
            SupervisorDecision(route=["weather"]),
            ToolPlan(),  # weather makes no calls
            SupervisorDecision(route=["weather"]),
            ToolPlan(),
        ])
        graph = build_graph(provider, ToolTrace(), tool_classes_by_agent={
            "research": {}, "task": {}, "weather": {"get_weather": FakeWeatherTool},
        })

        final = await _run(graph, "keep going")
        assert final["final_response"]  # loop ended with a response
        assert final["step"] <= 3
    finally:
        get_settings.cache_clear()
