"""Web search tool backed by the Tavily REST API (real HTTP calls)."""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.tools.base import Tool, ToolError

TAVILY_ENDPOINT = "https://api.tavily.com/search"


def _client() -> httpx.AsyncClient:
    """HTTP client factory (monkeypatched in tests with MockTransport)."""
    return httpx.AsyncClient(timeout=15.0)


class SearchWebArgs(BaseModel):
    query: str = Field(min_length=2, max_length=400)
    max_results: int = Field(default=5, ge=1, le=10)


class WebSearchTool(Tool):
    name = "search_web"
    description = "Search the public web and return titles, URLs and snippets."
    agent = "research"

    class Args(SearchWebArgs):
        pass

    async def _run(self, args: Args) -> list[dict[str, Any]]:
        settings = get_settings()
        if not settings.tavily_api_key:
            raise ToolError(
                "TAVILY_API_KEY is not configured; web search is unavailable.",
                retryable=False,
            )
        payload = {
            "api_key": settings.tavily_api_key,
            "query": args.query,
            "max_results": args.max_results,
            "search_depth": "basic",
        }
        try:
            async with _client() as client:
                response = await client.post(TAVILY_ENDPOINT, json=payload)
        except httpx.HTTPError as exc:
            # Network/timeout errors are worth retrying.
            raise ToolError(f"search request failed: {type(exc).__name__}", retryable=True) from exc

        if response.status_code in (429, 500, 502, 503, 504):
            raise ToolError(f"search API returned {response.status_code}", retryable=True)
        if response.status_code != 200:
            raise ToolError(f"search API returned {response.status_code}")

        data = response.json()
        results = [
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("content", "")[:300],
            }
            for item in data.get("results", [])
        ]
        if not results:
            raise ToolError("no search results found")
        return results
