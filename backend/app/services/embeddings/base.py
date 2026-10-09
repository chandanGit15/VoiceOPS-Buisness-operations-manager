"""
Embedding provider abstraction (Part 7).

Same pattern as the LLM/STT/TTS layers: agents and services depend only on
the interface + factory; the concrete vendor (Gemini today) is selected by
the EMBEDDING_PROVIDER environment variable.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from app.core.config import get_settings
from app.core.errors import APIError

logger = logging.getLogger("app.embeddings")


class EmbeddingError(APIError):
    status_code = 502
    code = "embedding_error"
    message = "Embedding generation failed."


class EmbeddingNotConfiguredError(EmbeddingError):
    status_code = 503
    code = "embedding_not_configured"
    message = "No embedding provider is configured."


class EmbeddingProvider(ABC):
    """Interface for text-embedding backends (batch oriented)."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def dimensions(self) -> int: ...

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts; returns one vector per input, same order."""


def get_embedding_provider() -> EmbeddingProvider:
    settings = get_settings()
    provider_name = settings.embedding_provider.lower()

    if provider_name == "gemini":
        from app.services.embeddings.gemini_embeddings import GeminiEmbeddingProvider

        return GeminiEmbeddingProvider()

    raise EmbeddingNotConfiguredError(
        f"Unknown EMBEDDING_PROVIDER '{settings.embedding_provider}'. Supported: 'gemini'."
    )
