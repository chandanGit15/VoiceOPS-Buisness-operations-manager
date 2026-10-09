"""
LLM provider abstraction.

Agents depend only on the LLMProvider interface and the factory in
get_llm_provider(); the concrete provider (Gemini today, another vendor
tomorrow) is selected by the LLM_PROVIDER environment variable.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from pydantic import BaseModel

from app.core.config import get_settings
from app.core.errors import APIError

logger = logging.getLogger("app.llm")


@dataclass
class LLMMessage:
    """A single message in an LLM conversation."""

    role: str  # "user" or "model"
    content: str


@dataclass
class LLMResponse:
    """Normalized provider response consumed by agents."""

    text: str
    provider: str
    model: str
    # Raw provider metadata for observability; never sent to clients.
    usage_metadata: dict[str, Any] | None = None


class LLMError(APIError):
    """Raised when the LLM provider fails or returns an unusable result."""

    status_code = 502
    code = "llm_error"
    message = "The language model could not complete the request."

    def __init__(self, message: str | None = None, *, retryable: bool = False, **kwargs) -> None:
        super().__init__(message, **kwargs)
        self.retryable = retryable


class LLMNotConfiguredError(LLMError):
    status_code = 503
    code = "llm_not_configured"
    message = "No LLM provider is configured. Set GEMINI_API_KEY in the environment."


class LLMProvider(ABC):
    """Interface every LLM backend must implement."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier (e.g. "gemini")."""

    @abstractmethod
    async def generate(
        self,
        messages: list[LLMMessage],
        *,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_output_tokens: int = 1024,
    ) -> LLMResponse:
        """Generate one assistant turn from the given conversation."""

    @abstractmethod
    async def generate_structured(
        self,
        messages: list[LLMMessage],
        schema: type[BaseModel],
        *,
        system_prompt: str | None = None,
        temperature: float = 0.0,
    ) -> BaseModel:
        """Generate a response validated against the given Pydantic schema.

        Raises LLMError when the model output cannot be parsed/validated.
        """


def get_llm_provider() -> LLMProvider:
    """Return the provider selected by the LLM_PROVIDER env var.

    Importing concrete providers lazily keeps optional SDKs optional:
    the app can boot without google-genai installed as long as no
    request reaches the provider.
    """
    settings = get_settings()
    provider_name = settings.llm_provider.lower()

    if provider_name == "gemini":
        from app.services.llm.gemini_provider import GeminiProvider

        return GeminiProvider()

    # Zero-cost dev/demo fallback: deterministic keyword routing, no API key
    # and no network. Same interface as the real provider; the eval harness
    # uses the identical heuristic (see eval/run_eval.py). Accuracy of this
    # heuristic is a property of the demo fallback, not of VoiceOps quality.
    if provider_name == "keyword":
        from app.services.llm.keyword_provider import KeywordHeuristicProvider

        return KeywordHeuristicProvider()

    raise LLMNotConfiguredError(
        f"Unknown LLM_PROVIDER '{settings.llm_provider}'. Supported: 'gemini', 'keyword'."
    )


async def with_llm_retries(
    operation: Callable[[], Awaitable[Any]],
    *,
    max_attempts: int | None = None,
) -> Any:
    """Run an LLM call with bounded retries for transient failures.

    Retries only LLMError marked retryable (network/timeout/5xx style);
    configuration errors and schema mismatches fail fast.
    """
    settings = get_settings()
    attempts = max_attempts if max_attempts is not None else settings.tool_max_retries + 1
    for attempt in range(attempts):
        try:
            return await operation()
        except LLMError as exc:
            if not exc.retryable or attempt == attempts - 1:
                raise
            logger.warning(
                "LLM call failed (attempt %d/%d): %s — retrying",
                attempt + 1, attempts, exc,
            )
            await asyncio.sleep(settings.tool_retry_delay_seconds * (attempt + 1))
