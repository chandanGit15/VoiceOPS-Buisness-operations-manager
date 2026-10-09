"""Tool registry: which tools exist and which agent may use them."""

from __future__ import annotations

from app.tools.base import Tool
from app.tools.calendar_tools import (
    CheckAvailabilityTool,
    CreateEventTool,
    DeleteEventTool,
    GetEventsTool,
    UpdateEventTool,
)
from app.tools.email_tools import (
    DraftEmailTool,
    ReadEmailTool,
    SearchEmailsTool,
    SendEmailTool,
)
from app.tools.knowledge_tools import (
    AnswerFromDocumentsTool,
    RetrieveRelevantChunksTool,
    SearchDocumentsTool,
)
from app.tools.tasks import CompleteTaskTool, CreateTaskTool, ListTasksTool
from app.tools.weather import WeatherTool
from app.tools.web_search import WebSearchTool

ALL_TOOLS: dict[str, type[Tool]] = {
    tool.name: tool
    for tool in (
        WebSearchTool, WeatherTool, CreateTaskTool, ListTasksTool, CompleteTaskTool,
        GetEventsTool, CheckAvailabilityTool, CreateEventTool, UpdateEventTool, DeleteEventTool,
        SearchEmailsTool, ReadEmailTool, DraftEmailTool, SendEmailTool,
        SearchDocumentsTool, RetrieveRelevantChunksTool, AnswerFromDocumentsTool,
    )
}

# Security: an agent may only call tools listed for it. Unknown or
# unauthorized tool names are rejected before execution.
AGENT_TOOLS: dict[str, dict[str, type[Tool]]] = {
    "supervisor": {},
    "research": {"search_web": WebSearchTool},
    "task": {"create_task": CreateTaskTool, "list_tasks": ListTasksTool, "complete_task": CompleteTaskTool},
    "weather": {"get_weather": WeatherTool},
    "calendar": {
        "get_events": GetEventsTool,
        "check_availability": CheckAvailabilityTool,
        "create_event": CreateEventTool,
        "update_event": UpdateEventTool,
        "delete_event": DeleteEventTool,
    },
    "email": {
        "search_emails": SearchEmailsTool,
        "read_email": ReadEmailTool,
        "draft_email": DraftEmailTool,
        "send_email": SendEmailTool,
    },
    "knowledge": {
        "search_documents": SearchDocumentsTool,
        "retrieve_relevant_chunks": RetrieveRelevantChunksTool,
        "answer_from_documents": AnswerFromDocumentsTool,
    },
}


def tools_for(agent_name: str) -> dict[str, type[Tool]]:
    """Return the tool classes the given agent is allowed to call."""
    return AGENT_TOOLS.get(agent_name, {})
