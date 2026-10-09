"""
Google Calendar + Gmail REST clients (httpx, no vendor SDK).

Every call resolves a fresh access token via google_oauth.get_valid_access_token,
so refresh is automatic and centralized. Errors become GoogleAPIError with
retryable marking; no token material is ever logged or returned.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import APIError
from app.models import User
from app.services import google_oauth

logger = logging.getLogger("app.google")

CALENDAR_BASE = "https://www.googleapis.com/calendar/v3"
GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1"


class GoogleAPIError(APIError):
    status_code = 502
    code = "google_api_error"
    message = "A Google API call failed."

    def __init__(self, message: str | None = None, *, retryable: bool = False, **kwargs) -> None:
        super().__init__(message, **kwargs)
        self.retryable = retryable


def _retryable(status: int) -> bool:
    return status in (429, 500, 502, 503, 504)


@dataclass
class GoogleContext:
    """Per-request context bundling the DB session and current user."""

    session: AsyncSession
    user: User

    async def authorized_headers(self) -> dict[str, str]:
        token = await google_oauth.get_valid_access_token(self.session, self.user)
        return {"Authorization": f"Bearer {token}"}


class GoogleClientBase:
    def __init__(self, context: GoogleContext) -> None:
        self.ctx = context

    async def _request(
        self, method: str, url: str, *, params: dict | None = None, json: dict | None = None
    ) -> httpx.Response:
        headers = await self.ctx.authorized_headers()
        if json is not None:
            headers["Content-Type"] = "application/json"
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.request(method, url, headers=headers, params=params, json=json)
        except httpx.HTTPError as exc:
            raise GoogleAPIError(f"Google request failed: {type(exc).__name__}", retryable=True) from exc

        if response.status_code in (401, 403):
            # Consent revoked or scopes insufficient — actionable for the user.
            raise GoogleAPIError(
                f"Google rejected the request ({response.status_code}). Reconnect Google in Settings.",
                code="google_auth_error",
            )
        if _retryable(response.status_code):
            raise GoogleAPIError(f"Google API returned {response.status_code}", retryable=True)
        if response.status_code >= 400:
            detail = ""
            try:
                detail = response.json().get("error", {}).get("message", "")
            except Exception:
                pass
            raise GoogleAPIError(f"Google API returned {response.status_code}: {detail}")
        return response


class CalendarClient(GoogleClientBase):
    """Thin wrapper over the Calendar v3 REST surface used by our tools."""

    async def list_events(self, *, time_min: str, time_max: str, max_results: int = 25) -> list[dict[str, Any]]:
        response = await self._request(
            "GET",
            f"{CALENDAR_BASE}/calendars/primary/events",
            params={
                "timeMin": time_min,
                "timeMax": time_max,
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": max_results,
            },
        )
        items = response.json().get("items", [])
        return [
            {
                "id": item.get("id"),
                "summary": item.get("summary", "(no title)"),
                "start": item.get("start", {}).get("dateTime") or item.get("start", {}).get("date"),
                "end": item.get("end", {}).get("dateTime") or item.get("end", {}).get("date"),
                "location": item.get("location"),
            }
            for item in items
        ]

    async def create_event(self, *, summary: str, start: str, end: str, description: str = "", location: str = "") -> dict[str, Any]:
        body: dict[str, Any] = {
            "summary": summary,
            "start": start.startswith("date:") and {"date": start[5:]} or {"dateTime": start},
            "end": end.startswith("date:") and {"date": end[5:]} or {"dateTime": end},
        }
        if description:
            body["description"] = description
        if location:
            body["location"] = location
        response = await self._request("POST", f"{CALENDAR_BASE}/calendars/primary/events", json=body)
        data = response.json()
        return {"id": data.get("id"), "summary": data.get("summary"), "html_link": data.get("htmlLink")}

    async def update_event(self, event_id: str, *, summary: str | None = None, start: str | None = None, end: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if summary:
            body["summary"] = summary
        if start:
            body["start"] = start.startswith("date:") and {"date": start[5:]} or {"dateTime": start}
        if end:
            body["end"] = end.startswith("date:") and {"date": end[5:]} or {"dateTime": end}
        response = await self._request("PATCH", f"{CALENDAR_BASE}/calendars/primary/events/{event_id}", json=body)
        data = response.json()
        return {"id": data.get("id"), "summary": data.get("summary"), "updated": data.get("updated")}

    async def delete_event(self, event_id: str) -> dict[str, Any]:
        response = await self._request("DELETE", f"{CALENDAR_BASE}/calendars/primary/events/{event_id}")
        return {"deleted": response.status_code in (200, 204, 410)}


class GmailClient(GoogleClientBase):
    """Gmail REST surface: search, read, draft, send."""

    async def search_emails(self, *, query: str, max_results: int = 10) -> list[dict[str, Any]]:
        response = await self._request(
            "GET", f"{GMAIL_BASE}/users/me/messages",
            params={"q": query, "maxResults": max_results},
        )
        stubs = response.json().get("messages", [])
        results = []
        for stub in stubs[:max_results]:
            detail = await self._request("GET", f"{GMAIL_BASE}/users/me/messages/{stub['id']}")
            data = detail.json()
            results.append(self._summarize_message(data))
        return results

    async def read_email(self, message_id: str) -> dict[str, Any]:
        response = await self._request("GET", f"{GMAIL_BASE}/users/me/messages/{message_id}")
        return self._summarize_message(response.json(), include_body=True)

    async def create_draft(self, *, to: str, subject: str, body: str) -> dict[str, Any]:
        raw = self._rfc2822(to=to, subject=subject, body=body)
        response = await self._request(
            "POST", f"{GMAIL_BASE}/users/me/drafts",
            json={"message": {"raw": base64.urlsafe_b64encode(raw.encode()).decode()}},
        )
        data = response.json()
        return {"draft_id": data.get("id"), "thread_id": data.get("threadId")}

    async def send_email(self, *, to: str, subject: str, body: str, draft_id: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "raw": base64.urlsafe_b64encode(self._rfc2822(to=to, subject=subject, body=body).encode()).decode()
        }
        if draft_id:
            payload["id"] = draft_id
        response = await self._request("POST", f"{GMAIL_BASE}/users/me/messages/send", json=payload)
        data = response.json()
        return {"message_id": data.get("id"), "thread_id": data.get("threadId")}

    # ---------- helpers ----------

    @staticmethod
    def _rfc2822(*, to: str, subject: str, body: str) -> str:
        # Minimal RFC 2822 message; addresses/subjects are header-encoded by Gmail.
        return f"To: {to}\r\nSubject: {subject}\r\nContent-Type: text/plain; charset=UTF-8\r\n\r\n{body}"

    @staticmethod
    def _extract_text(payload: dict[str, Any]) -> str:
        if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(payload["body"]["data"]).decode(errors="replace")
        for part in payload.get("parts", []):
            text = GmailClient._extract_text(part)
            if text:
                return text
        return ""

    def _summarize_message(self, data: dict[str, Any], *, include_body: bool = False) -> dict[str, Any]:
        headers = {h["name"].lower(): h["value"] for h in data.get("payload", {}).get("headers", [])}
        result = {
            "id": data.get("id"),
            "from": headers.get("from", ""),
            "to": headers.get("to", ""),
            "subject": headers.get("subject", ""),
            "date": headers.get("date", ""),
            "snippet": data.get("snippet", "")[:200],
        }
        if include_body:
            result["body"] = self._extract_text(data.get("payload", {}))[:4000]
        return result
