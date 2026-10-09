"""Data endpoints: conversations, tasks, agent activity, memories (Part 4)."""

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.database.session import get_session
from app.models import User
from app.services import persistence, task_store

logger = logging.getLogger("app.api.data")

router = APIRouter()


# ---------- Schemas ----------

class ConversationOut(BaseModel):
    id: str
    title: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    created_at: datetime | None = None


class ConversationDetailOut(ConversationOut):
    messages: list[MessageOut]


class TaskOut(BaseModel):
    id: str
    title: str
    description: str
    status: str
    priority: int
    due_date: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class TaskCreateIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    priority: int = Field(default=3, ge=1, le=5)
    due_date: datetime | None = None


class TraceEventOut(BaseModel):
    tool_name: str
    status: str
    output_data: dict | None = None
    created_at: datetime | None = None


class AgentRunOut(BaseModel):
    id: str
    agent_name: str
    status: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    tool_calls: list[TraceEventOut]


class MemoryOut(BaseModel):
    id: str
    memory_type: str
    content: str
    created_at: datetime | None = None


class MemoryCreateIn(BaseModel):
    memory_type: str = Field(pattern="^(fact|preference|note)$")
    content: str = Field(min_length=1, max_length=2000)


# ---------- Conversations ----------


@router.get("/conversations", response_model=list[ConversationOut], tags=["conversations"])
async def get_conversations(session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)) -> list[ConversationOut]:
    rows = await persistence.list_conversations(session, user.id)
    return [
        ConversationOut(
            id=str(c.id), title=c.title,
            created_at=c.created_at, updated_at=c.updated_at,
        )
        for c in rows
    ]


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailOut, tags=["conversations"])
async def get_conversation_detail(conversation_id: uuid.UUID, session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)) -> ConversationDetailOut:
    conversation = await persistence.get_conversation(session, conversation_id)
    # Ownership: strictly per-user. (Legacy user_id IS NULL rows were writeable
    # before auth and are NOT readable by authenticated users — foreign
    # conversations are indistinguishable from missing ones.)
    if conversation is None or conversation.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = await persistence.get_messages(session, conversation.id)
    return ConversationDetailOut(
        id=str(conversation.id),
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=[
            MessageOut(id=str(m.id), role=m.role, content=m.content, created_at=m.created_at)
            for m in messages
        ],
    )


# ---------- Tasks ----------


@router.get("/tasks", response_model=list[TaskOut], tags=["tasks"])
async def get_tasks(
    status_filter: str | None = Query(default=None, alias="status", pattern="^(open|completed)$"),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> list[TaskOut]:
    rows = await task_store.list_tasks(session, user_id=user.id, status=status_filter)
    return [_task_out(t) for t in rows]


@router.post("/tasks", response_model=TaskOut, status_code=status.HTTP_201_CREATED, tags=["tasks"])
async def create_task(payload: TaskCreateIn, session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)) -> TaskOut:
    task = await task_store.create_task(
        session, user_id=user.id, title=payload.title,
        description=payload.description, priority=payload.priority,
        due_date=payload.due_date,
    )
    return _task_out(task)


@router.post("/tasks/{task_id}/complete", response_model=TaskOut, tags=["tasks"])
async def complete_task(task_id: str, session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)) -> TaskOut:
    try:
        task = await task_store.complete_task(session, task_id, user_id=user.id)
    except ValueError as exc:
        # Foreign or malformed ids are indistinguishable: always 404.
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _task_out(task)


def _task_out(task) -> TaskOut:
    return TaskOut(
        id=str(task.id), title=task.title, description=task.description,
        status=task.status, priority=task.priority,
        due_date=task.due_date, created_at=task.created_at, updated_at=task.updated_at,
    )


# ---------- Agent activity ----------


@router.get("/activity", response_model=list[AgentRunOut], tags=["activity"])
async def get_activity(
    conversation_id: uuid.UUID | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> list[AgentRunOut]:
    runs = await persistence.list_agent_runs(session, conversation_id, limit)
    tool_rows = await persistence.list_tool_calls(session, [r.id for r in runs])
    by_run: dict = {}
    for record in tool_rows:
        by_run.setdefault(record.agent_run_id, []).append(record)
    return [
        AgentRunOut(
            id=str(r.id), agent_name=r.agent_name, status=r.status,
            started_at=r.started_at, completed_at=r.completed_at,
            tool_calls=[
                TraceEventOut(
                    tool_name=tc.tool_name, status=tc.status,
                    output_data=tc.output_data, created_at=tc.created_at,
                )
                for tc in by_run.get(r.id, [])
            ],
        )
        for r in runs
    ]


# ---------- Memories ----------


@router.get("/memories", response_model=list[MemoryOut], tags=["memories"])
async def get_memories(session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)) -> list[MemoryOut]:
    rows = await persistence.list_memories(session, user.id)
    return [
        MemoryOut(id=str(m.id), memory_type=m.memory_type, content=m.content, created_at=m.created_at)
        for m in rows
    ]


@router.post("/memories", response_model=MemoryOut, status_code=status.HTTP_201_CREATED, tags=["memories"])
async def create_memory(payload: MemoryCreateIn, session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)) -> MemoryOut:
    memory = await persistence.add_memory(
        session, user_id=user.id, memory_type=payload.memory_type, content=payload.content
    )
    return MemoryOut(id=str(memory.id), memory_type=memory.memory_type, content=memory.content, created_at=memory.created_at)
