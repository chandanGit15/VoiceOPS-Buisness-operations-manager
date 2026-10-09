"""Embedding provider layer."""

from app.services.embeddings.base import (
    EmbeddingError,
    EmbeddingNotConfiguredError,
    EmbeddingProvider,
    get_embedding_provider,
)

__all__ = [
    "EmbeddingError",
    "EmbeddingNotConfiguredError",
    "EmbeddingProvider",
    "get_embedding_provider",
]
