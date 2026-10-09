"""Tests for POST /api/v1/chat (fake LLM provider + SQLite test database)."""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.agents.supervisor import SupervisorAgent
from tests.fakes import FakeProvider

pytestmark = pytest.mark.asyncio


async def _client_with(patched_db, provider: FakeProvider) -> AsyncClient:
    """Build an app wired to the given fake provider and the test database."""
    import app.api.chat as chat_module

    chat_module.supervisor = SupervisorAgent(provider=provider)
    return AsyncClient(transport=ASGITransport(app=patched_db), base_url="http://test")


async def test_chat_returns_structured_response_with_trace(patched_db) -> None:
    provider = FakeProvider(structured_queue=[
        {"respond_directly": "Hello from the fake agent!"},
    ])
    async with await _client_with(patched_db, provider) as client:
        response = await client.post("/api/v1/chat", json={"message": "Hello"})
        assert response.status_code == 200
        body = response.json()

        assert body["message"] == "Hello from the fake agent!"
        assert body["agent"] == "supervisor"
        # Direct answer: only the supervisor routing event, no tool events.
        assert [e["agent"] for e in body["trace"]] == ["supervisor"]
        assert body["trace"][0]["action"] == "route"
        uuid.UUID(body["session_id"])  # session id is a valid UUID


async def test_chat_session_continuity_and_history(patched_db) -> None:
    provider = FakeProvider()
    async with await _client_with(patched_db, provider) as client:
        first = (await client.post("/api/v1/chat", json={"message": "hi"})).json()
        await client.post(
            "/api/v1/chat",
            json={"message": "again", "session_id": first["session_id"]},
        )

        # The second supervisor decision prompt must include the prior turns.
        decisions = [
            c for c in provider.structured_calls if c["schema"] == "SupervisorDecision"
        ]
        assert len(decisions) == 2
        second_prompt = decisions[1]["system_prompt"]
        assert "hi" in second_prompt
        assert "FAKE REPLY" in second_prompt  # prior assistant turn is present


async def test_chat_rejects_empty_message(patched_db) -> None:
    provider = FakeProvider()
    async with await _client_with(patched_db, provider) as client:
        response = await client.post("/api/v1/chat", json={"message": ""})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"


async def test_chat_llm_failure_returns_error_envelope(patched_db) -> None:
    from app.services.llm import LLMError

    provider = FakeProvider(structured_queue=[LLMError("Gemini request failed: TestExplosion")])
    async with await _client_with(patched_db, provider) as client:
        response = await client.post("/api/v1/chat", json={"message": "hello"})

        assert response.status_code == 502
        body = response.json()
        assert body["status"] == "error"
        assert body["error"]["code"] == "llm_error"
