"""
Task persistence (Part 4): the previous in-memory TaskStore is replaced by
real database rows via SQLAlchemy. The operation surface (create/list/
complete) is unchanged, so the task tools only switch to `await`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Task


def task_to_dict(task: Task) -> dict[str, Any]:
    return {
        "id": str(task.id),
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "priority": task.priority,
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
    }


async def create_task(
    session: AsyncSession,
    *,
    user_id: Any,
    title: str,
    description: str = "",
    priority: int = 3,
    due_date: datetime | None = None,
) -> Task:
    task = Task(
        user_id=user_id,
        title=title.strip()[:255],
        description=description.strip(),
        priority=max(1, min(5, priority)),
        due_date=due_date,
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return task


async def list_tasks(
    session: AsyncSession,
    *,
    user_id: Any = None,
    status: str | None = None,
    limit: int = 100,
) -> list[Task]:
    query = select(Task).order_by(desc(Task.created_at)).limit(limit)
    if user_id is not None:
        query = query.where(Task.user_id == user_id)
    if status:
        query = query.where(Task.status == status)
    result = await session.execute(query)
    return list(result.scalars())


async def complete_task(session: AsyncSession, task_id: str, *, user_id: Any = None) -> Task:
    """Mark a task completed. Raises ValueError for unknown/malformed ids.

    With user_id set, foreign tasks are treated exactly like missing ones.
    """
    try:
        task_uuid = uuid.UUID(task_id)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"invalid task id: {task_id!r}") from exc

    query = select(Task).where(Task.id == task_uuid)
    if user_id is not None:
        query = query.where((Task.user_id == user_id) | (Task.user_id.is_(None)))
    result = await session.execute(query)
    task = result.scalar_one_or_none()
    if task is None:
        raise ValueError(f"task {task_id!r} not found")

    task.status = "completed"
    task.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(task)
    return task
