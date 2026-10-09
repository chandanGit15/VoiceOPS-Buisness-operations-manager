"""Chat endpoints for the API v1 surface (Parts 2-4)."""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.supervisor import SupervisorAgent
from app.core.rate_limit import rate_limit
from app.core.security import get_current_user
from app.database.session import get_session
from app.models import User
from app.schemas.chat import ChatRequest, ChatResponse, TraceEvent
from app.services import persistence
from app.services.llm import LLMMessage

logger = logging.getLogger("app.api.chat")

router = APIRouter(dependencies=[Depends(rate_limit(window_seconds=60, per_minute_setting="chat_rate_limit_per_min"))])

# One agent instance per process; the LLM provider it uses is cheap to share.
supervisor = SupervisorAgent()

HISTORY_MESSAGE_LIMIT = 30  # short-term memory window (most recent turns)


@router.post(
    "/chat",
    response_model=ChatResponse,
    status_code=status.HTTP_200_OK,
    summary="Send a message to the supervisor agent",
    tags=["chat"],
)
async def chat(
    payload: ChatRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> ChatResponse:
    """Handle one chat turn: persist/load history, run the multi-agent graph,
    store messages + execution history, return the structured response."""
    # Resolve or create the conversation (title = first user message snippet).
    conversation = None
    if payload.session_id:
        try:
            conversation = await persistence.get_conversation(session, uuid.UUID(payload.session_id))
        except ValueError:
            conversation = None  # malformed id → start fresh, don't 500
        # Authorization: a foreign session id must be indistinguishable from
        # an unknown one (silently start a fresh conversation, never 403).
        if conversation is not None and conversation.user_id != user.id:
            conversation = None
    if conversation is None:
        conversation = await persistence.create_conversation(
            session, user.id, payload.message[:80]
        )

    # Short-term memory: recent turns from the database (not in-process),
    # so history survives restarts.
    rows = await persistence.get_messages(session, conversation.id, limit=HISTORY_MESSAGE_LIMIT)
    history = [LLMMessage(role=m.role, content=m.content) for m in rows]

    # Long-term memory: inject durable facts/preferences as context.
    memories = await persistence.list_memories(session, user.id, limit=20)
    if memories:
        memory_block = "\n".join(f"- [{m.memory_type}] {m.content}" for m in memories)
        history.insert(0, LLMMessage(role="user", content=f"[Long-term memory about the user]\n{memory_block}"))

    started_at = datetime.now(timezone.utc)
    # Google tools receive this context (session + user) for OAuth and
    # approval rows; non-Google tools ignore it.
    result = await supervisor.handle(
        history=history,
        user_message=payload.message,
        tool_context={"session": session, "user": user},
    )

    # Persist both turns + the agent execution history.
    await persistence.append_message(session, conversation.id, "user", payload.message)
    await persistence.append_message(session, conversation.id, "model", result.message)

    run = await persistence.record_agent_run(
        session,
        conversation_id=conversation.id,
        agent_name="supervisor",
        status="ok",
        started_at=started_at,
    )
    for event in result.trace:
        await persistence.record_tool_call(
            session,
            agent_run_id=run.id,
            tool_name=event.get("tool") or event.get("action", "unknown"),
            status=event.get("status", "ok"),
            input_data=None,  # safe metadata only; raw payloads stay out of the DB
            output_data={"duration_ms": event.get("duration_ms"), "error": event.get("error")},
        )
    await session.commit()

    return ChatResponse(
        message=result.message,
        session_id=str(conversation.id),
        agent="supervisor",
        trace=[TraceEvent(**event) for event in result.trace[-50:]],
    )
