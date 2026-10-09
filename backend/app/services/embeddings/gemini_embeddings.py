"""Gemini embedding adapter (text-embedding-004 via google-genai, async)."""

from __future__ import annotations

from google import genai

from app.core.config import get_settings
from app.services.embeddings.base import EmbeddingError, EmbeddingProvider


class GeminiEmbeddingProvider(EmbeddingProvider):
    def __init__(self) -> None:
        settings = get_settings()
        if not settings.gemini_api_key:
            from app.services.embeddings.base import EmbeddingNotConfiguredError

            raise EmbeddingNotConfiguredError(
                "GEMINI_API_KEY is not set; cannot use the Gemini embedding provider."
            )
        self._model = settings.embedding_model
        self._dimensions = settings.embedding_dimensions
        # Embeddings share the Gemini API key with the LLM provider.
        self._client = genai.Client(api_key=settings.gemini_api_key)

    @property
    def name(self) -> str:
        return "gemini"

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = await self._client.aio.models.embed_content(
                model=self._model,
                contents=[t[:8000] for t in texts],  # vendor input cap per doc
            )
        except Exception as exc:
            raise EmbeddingError(f"Gemini embedding request failed: {type(exc).__name__}") from exc

        embeddings = response.embeddings or []
        if len(embeddings) != len(texts):
            raise EmbeddingError(
                f"Gemini returned {len(embeddings)} embeddings for {len(texts)} inputs."
            )
        return [list(e.values) for e in embeddings]
