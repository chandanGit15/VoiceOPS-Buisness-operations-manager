"""
Rate limiting (Part 8): per-user sliding window, in-process.

Single-process dev limiter (Redis-backed distributed limiting is the
documented next step for multi-worker deployments). Limits come from
settings each call, so tests/ops can tune them via env without restarts
in single-process mode.

Exceeding the window returns 429 with the standard error envelope.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Callable

from fastapi import Depends, HTTPException, Request, status

from app.core.config import get_settings
from app.core.security import get_current_user
from app.models import User

_lock = threading.Lock()
_buckets: dict[str, deque] = defaultdict(deque)


def rate_limit(*, window_seconds: int, per_minute_setting: str) -> Callable:
    """Build a dependency limiting requests per user (or IP) per window."""

    def dependency(request: Request, user: User | None = Depends(get_current_user)) -> None:
        settings = get_settings()
        limit = int(getattr(settings, per_minute_setting))
        key = f"{user.id if user else request.client.host}:{per_minute_setting}"
        now = time.monotonic()

        with _lock:
            bucket = _buckets[key]
            while bucket and now - bucket[0] > window_seconds:
                bucket.popleft()
            if len(bucket) >= limit:
                retry_after = int(window_seconds - (now - bucket[0])) + 1
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail={
                        "code": "rate_limited",
                        "message": f"Rate limit exceeded. Retry in {retry_after}s.",
                    },
                    headers={"Retry-After": str(retry_after)},
                )
            bucket.append(now)

    return dependency
