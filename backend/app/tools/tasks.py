"""Task management tools backed by the database (real persistence)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.database import session as db_session
from app.services import task_store
from app.tools.base import Tool, ToolError


class CreateTaskArgs(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    due_date: Optional[str] = Field(default=None, max_length=32)
    priority: int = Field(default=3, ge=1, le=5)


class CompleteTaskArgs(BaseModel):
    task_id: str = Field(min_length=1, max_length=64)


class CreateTaskTool(Tool):
    name = "create_task"
    description = "Create a new task with a title, optional description, due date and priority."
    agent = "task"

    class Args(CreateTaskArgs):
        pass

    async def _run(self, args: Args) -> dict:
        due_date = None
        if args.due_date:
            try:
                due_date = datetime.fromisoformat(args.due_date)
            except ValueError:
                # Accept plain dates like "2026-10-09" too.
                try:
                    due_date = datetime.fromisoformat(f"{args.due_date}T00:00:00")
                except ValueError:
                    raise ToolError(f"invalid due_date format: {args.due_date!r} (use YYYY-MM-DD)")

        user = self.ctx.get("user")
        owner_id = user.id if user else None

        session = self.ctx.get("session")
        if session is not None:
            # Reuse the request session (chat path); caller commits.
            task = await task_store.create_task(
                session,
                user_id=owner_id,
                title=args.title,
                description=args.description,
                priority=args.priority,
                due_date=due_date,
            )
            return task_store.task_to_dict(task)

        async with db_session.get_session_factory()() as session:
            task = await task_store.create_task(
                session,
                user_id=owner_id,
                title=args.title,
                description=args.description,
                priority=args.priority,
                due_date=due_date,
            )
            return task_store.task_to_dict(task)


class ListTasksTool(Tool):
    name = "list_tasks"
    description = "List tasks, optionally filtered by status ('open' or 'completed')."
    agent = "task"

    class Args(BaseModel):
        status: Optional[str] = Field(default=None, pattern="^(open|completed)$")

    async def _run(self, args: Args) -> dict:
        user = self.ctx.get("user")
        owner_id = user.id if user else None

        session = self.ctx.get("session")
        if session is not None:
            tasks = await task_store.list_tasks(session, user_id=owner_id, status=args.status)
            return {"count": len(tasks), "tasks": [task_store.task_to_dict(t) for t in tasks]}

        async with db_session.get_session_factory()() as session:
            tasks = await task_store.list_tasks(session, user_id=owner_id, status=args.status)
            return {"count": len(tasks), "tasks": [task_store.task_to_dict(t) for t in tasks]}


class CompleteTaskTool(Tool):
    name = "complete_task"
    description = "Mark an existing task as completed by its id."
    agent = "task"

    class Args(CompleteTaskArgs):
        pass

    async def _run(self, args: Args) -> dict:
        async with db_session.get_session_factory()() as session:
            try:
                task = await task_store.complete_task(session, args.task_id)
            except ValueError as exc:
                raise ToolError(str(exc)) from exc
            return task_store.task_to_dict(task)
