"""Tests for tool execution: success, failure, validation, trace events."""

import httpx
import pytest

from app.tools.base import ToolError, ToolTrace
from app.tools.tasks import CompleteTaskTool, CreateTaskTool, ListTasksTool
from app.tools.weather import WeatherTool
from app.tools.web_search import WebSearchTool

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def patched_task_db(db_session_factory, monkeypatch):
    """Task tools must hit the test database, never real Postgres."""
    from app.database import session as db_session

    monkeypatch.setattr(
        db_session, "get_session_factory", lambda: db_session_factory
    )
    yield


def _mock_client(monkeypatch, module, handler):
    """Point a tool module's _client() at an httpx.MockTransport."""
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(module, "_client", lambda: httpx.AsyncClient(transport=transport))


# ---------- Task tools (now DB-backed) ----------


async def test_create_list_complete_task_roundtrip(db_session_factory):
    trace = ToolTrace()
    created = await CreateTaskTool(trace=trace).run(title="Carry an umbrella", due_date="2026-10-09")
    assert created["status"] == "open"

    listing = await ListTasksTool(trace=trace).run()
    assert listing["count"] == 1
    assert listing["tasks"][0]["title"] == "Carry an umbrella"

    done = await CompleteTaskTool(trace=trace).run(task_id=created["id"])
    assert done["status"] == "completed"

    assert [e["tool"] for e in trace.events] == ["create_task", "list_tasks", "complete_task"]
    assert all(e["status"] == "ok" for e in trace.events)


async def test_complete_task_unknown_id_raises_and_traces_error():
    trace = ToolTrace()
    with pytest.raises(ToolError, match="invalid task id"):
        await CompleteTaskTool(trace=trace).run(task_id="nope")
    assert trace.events[-1]["status"] == "error"
    assert "invalid task id" in trace.events[-1]["error"]


async def test_create_task_rejects_empty_title():
    trace = ToolTrace()
    with pytest.raises(ToolError):
        await CreateTaskTool(trace=trace).run(title="")
    assert trace.events[-1]["error"] == "invalid arguments"


async def test_create_task_rejects_bad_due_date():
    trace = ToolTrace()
    with pytest.raises(ToolError, match="due_date"):
        await CreateTaskTool(trace=trace).run(title="x", due_date="not-a-date")


# ---------- Weather tool ----------


async def test_weather_tool_success(monkeypatch):
    from app.tools import weather as weather_module

    def handler(request: httpx.Request) -> httpx.Response:
        if "geocoding" in str(request.url):
            return httpx.Response(200, json={
                "results": [{"name": "London", "country": "United Kingdom", "latitude": 51.5, "longitude": -0.12}]
            })
        return httpx.Response(200, json={
            "daily": {
                "time": ["2026-10-08", "2026-10-09"],
                "weather_code": [61, 80],
                "temperature_2m_max": [18.1, 17.0],
                "temperature_2m_min": [10.2, 9.1],
                "precipitation_probability_max": [20, 85],
            }
        })

    _mock_client(monkeypatch, weather_module, handler)
    trace = ToolTrace()
    result = await WeatherTool(trace=trace).run(city="London", days=2)

    assert result["city"] == "London"
    assert result["days"][0]["condition"] == "light rain"
    assert trace.events[0]["status"] == "ok"


async def test_weather_tool_unknown_city(monkeypatch):
    from app.tools import weather as weather_module

    _mock_client(monkeypatch, weather_module, lambda req: httpx.Response(200, json={"results": None}))

    with pytest.raises(ToolError, match="unknown city"):
        await WeatherTool().run(city="Atlantis")


async def test_weather_tool_network_error_is_retried_then_reported(monkeypatch):
    from app.tools import weather as weather_module

    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        raise httpx.ConnectError("connection refused")

    _mock_client(monkeypatch, weather_module, handler)
    monkeypatch.setenv("TOOL_RETRY_DELAY_SECONDS", "0")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        trace = ToolTrace()
        with pytest.raises(ToolError):
            await WeatherTool(trace=trace).run(city="London")

        # Retried (1 initial + 2 retries), and the final trace event is an error.
        assert attempts["count"] == 3
        assert trace.events[-1]["status"] == "error"
    finally:
        get_settings.cache_clear()


# ---------- Web search tool ----------


async def test_web_search_success(monkeypatch):
    from app.tools import web_search as search_module

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "results": [{"title": "Result", "url": "https://x.example", "content": "snippet text"}]
        })

    _mock_client(monkeypatch, search_module, handler)
    monkeypatch.setenv("TAVILY_API_KEY", "fake-key")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        result = await WebSearchTool().run(query="python langgraph")
        assert result[0]["title"] == "Result"
        assert result[0]["snippet"] == "snippet text"
    finally:
        get_settings.cache_clear()


async def test_web_search_requires_api_key(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        with pytest.raises(ToolError, match="TAVILY_API_KEY"):
            await WebSearchTool().run(query="hello world")
    finally:
        get_settings.cache_clear()
