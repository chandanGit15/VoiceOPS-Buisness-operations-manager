"""
Database persistence helpers for conversations, messages, agent runs,
tool-call history, and memories.

Pure data-access functions taking a session; no HTTP concepts here.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentRun, Conversation, Memory, Message, ToolCallRecord, User


async def get_or_create_default_user(session: AsyncSession) -> User:
    """Single-user mode for now: one dev user; OAuth arrives in Part 5+."""
    result = await session.execute(select(User).where(User.email == "dev@voiceops.local"))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(email="dev@voiceops.local", name="Local User")
        session.add(user)
        await session.commit()
        await session.refresh(user)
    return user


# ---------- Conversations & messages ----------


async def create_conversation(session: AsyncSession, user_id: uuid.UUID | None, title: str) -> Conversation:
    conversation = Conversation(user_id=user_id, title=title[:255] or "New conversation")
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return conversation


async def get_conversation(session: AsyncSession, conversation_id: uuid.UUID) -> Conversation | None:
    result = await session.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    return result.scalar_one_or_none()


async def list_conversations(session: AsyncSession, user_id: uuid.UUID | None, limit: int = 50) -> list[Conversation]:
    query = select(Conversation).order_by(desc(Conversation.updated_at)).limit(limit)
    if user_id is not None:
        query = query.where(Conversation.user_id == user_id)
    result = await session.execute(query)
    return list(result.scalars())


async def append_message(
    session: AsyncSession, conversation_id: uuid.UUID, role: str, content: str
) -> Message:
    message = Message(conversation_id=conversation_id, role=role, content=content)
    session.add(message)
    await session.commit()
    await session.refresh(message)
    return message


async def get_messages(session: AsyncSession, conversation_id: uuid.UUID, limit: int = 100) -> list[Message]:
    result = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at)
        .limit(limit)
    )
    return list(result.scalars())


# ---------- Agent execution history ----------


async def record_agent_run(
    session: AsyncSession,
    *,
    conversation_id: uuid.UUID | None,
    agent_name: str,
    status: str,
    started_at: datetime,
    completed_at: datetime | None = None,
) -> AgentRun:
    run = AgentRun(
        conversation_id=conversation_id,
        agent_name=agent_name,
        status=status,
        started_at=started_at,
        completed_at=completed_at or datetime.now(timezone.utc),
    )
    session.add(run)
    await session.flush()  # assign run.id without committing
    return run


async def record_tool_call(
    session: AsyncSession,
    *,
    agent_run_id: uuid.UUID | None,
    tool_name: str,
    status: str,
    input_data: dict[str, Any] | None,
    output_data: dict[str, Any] | None,
) -> ToolCallRecord:
    record = ToolCallRecord(
        agent_run_id=agent_run_id,
        tool_name=tool_name,
        status=status,
        input_data=input_data,
        output_data=output_data,
    )
    session.add(record)
    return record


async def list_agent_runs(session: AsyncSession, conversation_id: uuid.UUID | None = None, limit: int = 50) -> list[AgentRun]:
    query = select(AgentRun).order_by(desc(AgentRun.started_at)).limit(limit)
    if conversation_id is not None:
        query = query.where(AgentRun.conversation_id == conversation_id)
    result = await session.execute(query)
    return list(result.scalars())


async def list_tool_calls(session: AsyncSession, agent_run_ids: list[uuid.UUID]) -> list[ToolCallRecord]:
    if not agent_run_ids:
        return []
    result = await session.execute(
        select(ToolCallRecord)
        .where(ToolCallRecord.agent_run_id.in_(agent_run_ids))
        .order_by(ToolCallRecord.created_at)
    )
    return list(result.scalars())


# ---------- Memory ----------


async def add_memory(
    session: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    memory_type: str,
    content: str,
    metadata: dict[str, Any] | None = None,
) -> Memory:
    memory = Memory(user_id=user_id, memory_type=memory_type, content=content, metadata_json=metadata)
    session.add(memory)
    await session.commit()
    await session.refresh(memory)
    return memory


async def list_memories(session: AsyncSession, user_id: uuid.UUID | None, memory_type: str | None = None, limit: int = 50) -> list[Memory]:
    query = select(Memory).order_by(desc(Memory.created_at)).limit(limit)
    if user_id is not None:
        query = query.where(Memory.user_id == user_id)
    if memory_type:
        query = query.where(Memory.memory_type == memory_type)
    result = await session.execute(query)
    return list(result.scalars())
