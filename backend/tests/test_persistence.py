"""Database persistence tests (SQLite via aiosqlite — same models as Postgres)."""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio

from app.services import persistence, task_store  # noqa: E402


async def test_task_crud_roundtrip(db_session_factory):
    async with db_session_factory() as session:
        user = await persistence.get_or_create_default_user(session)

        created = await task_store.create_task(
            session, user_id=user.id, title="Carry an umbrella", priority=2
        )
        assert created.status == "open"
        assert created.priority == 2

        rows = await task_store.list_tasks(session, user_id=user.id, status="open")
        assert [t.title for t in rows] == ["Carry an umbrella"]

        completed = await task_store.complete_task(session, str(created.id))
        assert completed.status == "completed"

        with pytest.raises(ValueError, match="not found"):
            await task_store.complete_task(session, str(uuid.uuid4()))


async def test_conversation_and_message_persistence(db_session_factory):
    async with db_session_factory() as session:
        user = await persistence.get_or_create_default_user(session)
        conversation = await persistence.create_conversation(session, user.id, "Test chat")

        await persistence.append_message(session, conversation.id, "user", "hi")
        await persistence.append_message(session, conversation.id, "model", "hello")

        rows = await persistence.get_messages(session, conversation.id)
        assert [(m.role, m.content) for m in rows] == [("user", "hi"), ("model", "hello")]

        # Default user is idempotent (single-user bootstrap).
        again = await persistence.get_or_create_default_user(session)
        assert again.id == user.id


async def test_agent_run_and_tool_call_history(db_session_factory):
    from datetime import datetime, timezone

    async with db_session_factory() as session:
        user = await persistence.get_or_create_default_user(session)
        conversation = await persistence.create_conversation(session, user.id, "runs")

        run = await persistence.record_agent_run(
            session, conversation_id=conversation.id, agent_name="supervisor",
            status="ok", started_at=datetime.now(timezone.utc),
        )
        await persistence.record_tool_call(
            session, agent_run_id=run.id, tool_name="get_weather", status="ok",
            input_data=None, output_data={"duration_ms": 12.5, "error": None},
        )
        await persistence.record_tool_call(
            session, agent_run_id=run.id, tool_name="create_task", status="error",
            input_data=None, output_data={"error": "task not found"},
        )
        await session.commit()

        runs = await persistence.list_agent_runs(session, conversation.id)
        assert len(runs) == 1
        calls = await persistence.list_tool_calls(session, [runs[0].id])
        assert [c.tool_name for c in calls] == ["get_weather", "create_task"]
        assert [c.status for c in calls] == ["ok", "error"]


async def test_memory_add_and_list(db_session_factory):
    async with db_session_factory() as session:
        user = await persistence.get_or_create_default_user(session)
        await persistence.add_memory(
            session, user_id=user.id, memory_type="preference", content="Prefers short answers"
        )
        rows = await persistence.list_memories(session, user.id)
        assert len(rows) == 1
        assert rows[0].content == "Prefers short answers"


async def test_data_rest_endpoints(patched_db):
    """Conversations/tasks/activity REST endpoints against the test database."""
    transport = ASGITransport(app=patched_db)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/v1/tasks", json={"title": "REST-created task", "priority": 1}
        )
        assert created.status_code == 201
        assert created.json()["status"] == "open"

        listing = await client.get("/api/v1/tasks")
        assert listing.status_code == 200
        assert any(t["title"] == "REST-created task" for t in listing.json())

        completed = await client.post(f"/api/v1/tasks/{created.json()['id']}/complete")
        assert completed.status_code == 200
        assert completed.json()["status"] == "completed"

        conversations = await client.get("/api/v1/conversations")
        assert conversations.status_code == 200

        activity = await client.get("/api/v1/activity")
        assert activity.status_code == 200

        memory = await client.post(
            "/api/v1/memories", json={"memory_type": "fact", "content": "Testing memories"}
        )
        assert memory.status_code == 201


async def test_chat_persists_conversation_and_history(patched_db, monkeypatch):
    """A chat turn writes conversation + messages + agent_run + tool_calls rows."""
    import app.api.chat as chat_module
    from app.agents.supervisor import SupervisorAgent
    from app.main import create_app  # noqa: F401 — app already wired by patched_db
    from tests.fakes import FakeProvider

    chat_module.supervisor = SupervisorAgent(provider=FakeProvider(text="Persistent reply"))

    transport = ASGITransport(app=patched_db)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/v1/chat", json={"message": "Remember this"})
        assert response.status_code == 200
        session_id = response.json()["session_id"]

        # The conversation is retrievable with both turns.
        detail = await client.get(f"/api/v1/conversations/{session_id}")
        assert detail.status_code == 200
        roles = [m["role"] for m in detail.json()["messages"]]
        assert roles == ["user", "model"]

        # A second turn passes prior history to the agent (short-term memory):
        # the supervisor decision prompt must contain the earlier turns.
        await client.post(
            "/api/v1/chat", json={"message": "What did I just say?", "session_id": session_id}
        )
        provider = chat_module.supervisor._provider
        decisions = [
            c for c in provider.structured_calls if c["schema"] == "SupervisorDecision"
        ]
        assert len(decisions) == 2
        assert "Remember this" in decisions[1]["system_prompt"]  # prior turn loaded from DB

        activity = await client.get("/api/v1/activity")
        runs = activity.json()
        assert len(runs) >= 2
        assert runs[0]["agent_name"] == "supervisor"
