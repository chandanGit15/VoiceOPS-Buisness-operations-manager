"""Calendar Agent tools (real Google Calendar API via OAuth)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from pydantic import BaseModel, Field

from app.tools.base import ToolError
from app.tools.google_base import ApprovalGateTool, GoogleTool


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


class GetEventsArgs(BaseModel):
    start: str = Field(description="Window start, ISO 8601 (e.g. 2026-10-09T00:00:00)")
    end: str = Field(description="Window end, ISO 8601")
    max_results: int = Field(default=25, ge=1, le=50)


class GetEventsTool(GoogleTool):
    name = "get_events"
    description = "List calendar events in a time window (read-only)."
    agent = "calendar"

    class Args(GetEventsArgs):
        pass

    async def _run(self, args: Args) -> dict:
        try:
            start = datetime.fromisoformat(args.start)
            end = datetime.fromisoformat(args.end)
        except ValueError as exc:
            raise ToolError(f"invalid ISO datetime: {exc}") from exc
        events = await self.calendar.list_events(
            time_min=_iso(start), time_max=_iso(end), max_results=args.max_results
        )
        return {"count": len(events), "events": events}


class CheckAvailabilityArgs(BaseModel):
    date: str = Field(description="Day to check, ISO date (e.g. 2026-10-09)")
    duration_minutes: int = Field(default=30, ge=5, le=480)
    work_day_start: str = Field(default="09:00")
    work_day_end: str = Field(default="17:00")


class CheckAvailabilityTool(GoogleTool):
    name = "check_availability"
    description = "Find free slots of a given duration on a day (read-only)."
    agent = "calendar"

    class Args(CheckAvailabilityArgs):
        pass

    async def _run(self, args: Args) -> dict:
        try:
            day = datetime.fromisoformat(args.date).date()
        except ValueError as exc:
            raise ToolError(f"invalid date: {args.date!r}") from exc

        tz = timezone.utc
        window_start = datetime.fromisoformat(f"{args.date}T{args.work_day_start}:00").replace(tzinfo=tz)
        window_end = datetime.fromisoformat(f"{args.date}T{args.work_day_end}:00").replace(tzinfo=tz)

        events = await self.calendar.list_events(time_min=_iso(window_start), time_max=_iso(window_end))
        busy = []
        for event in events:
            if not event.get("start") or not event.get("end"):
                continue
            try:
                b_start = datetime.fromisoformat(str(event["start"]).replace("Z", "+00:00"))
                b_end = datetime.fromisoformat(str(event["end"]).replace("Z", "+00:00"))
            except ValueError:
                continue
            busy.append((max(b_start, window_start), min(b_end, window_end)))

        # Sweep the window, collecting gaps >= requested duration.
        free = []
        cursor = window_start
        duration = timedelta(minutes=args.duration_minutes)
        for b_start, b_end in sorted(busy):
            if b_start - cursor >= duration:
                free.append({"start": _iso(cursor), "end": _iso(b_start)})
            cursor = max(cursor, b_end)
        if window_end - cursor >= duration:
            free.append({"start": _iso(cursor), "end": _iso(window_end)})

        return {
            "date": str(day),
            "duration_minutes": args.duration_minutes,
            "free_slots": free[:10],
            "busy_events": len(events),
        }


class CreateEventArgs(BaseModel):
    summary: str = Field(min_length=1, max_length=200)
    start: str = Field(description="Event start, ISO 8601 datetime (or 'date:YYYY-MM-DD' for all-day)")
    end: str = Field(description="Event end, ISO 8601 datetime (or 'date:YYYY-MM-DD' for all-day)")
    description: str = Field(default="", max_length=2000)
    location: str = Field(default="", max_length=200)


class CreateEventTool(ApprovalGateTool):
    name = "create_event"
    description = "Create a calendar event (requires human approval)."
    agent = "calendar"
    action = "calendar.create_event"
    risk_level = "medium"

    class Args(CreateEventArgs):
        pass

    def _payload_summary(self, args: Args) -> str:
        return f"Create event '{args.summary}' starting {args.start}"

    async def _execute(self, args: Args) -> dict:
        return await self.calendar.create_event(
            summary=args.summary, start=args.start, end=args.end,
            description=args.description, location=args.location,
        )


class UpdateEventArgs(BaseModel):
    event_id: str = Field(min_length=1, max_length=128)
    summary: Optional[str] = Field(default=None, max_length=200)
    start: Optional[str] = Field(default=None, max_length=64)
    end: Optional[str] = Field(default=None, max_length=64)


class UpdateEventTool(ApprovalGateTool):
    name = "update_event"
    description = "Modify an existing calendar event (requires human approval)."
    agent = "calendar"
    action = "calendar.update_event"
    risk_level = "medium"

    class Args(UpdateEventArgs):
        pass

    def _payload_summary(self, args: Args) -> str:
        return f"Update event {args.event_id}"

    async def _execute(self, args: Args) -> dict:
        return await self.calendar.update_event(
            args.event_id, summary=args.summary, start=args.start, end=args.end
        )


class DeleteEventArgs(BaseModel):
    event_id: str = Field(min_length=1, max_length=128)


class DeleteEventTool(ApprovalGateTool):
    name = "delete_event"
    description = "Delete a calendar event (requires approval; HIGH RISK)."
    agent = "calendar"
    action = "calendar.delete_event"
    risk_level = "high"

    class Args(DeleteEventArgs):
        pass

    def _payload_summary(self, args: Args) -> str:
        return f"Delete calendar event {args.event_id}"

    async def _execute(self, args: Args) -> dict:
        return await self.calendar.delete_event(args.event_id)
