"""Test doubles shared across the test suite."""

from typing import Any

from pydantic import BaseModel

from app.services.llm import LLMMessage, LLMProvider, LLMResponse


class FakeProvider(LLMProvider):
    """Deterministic provider: canned text replies plus a scripted queue of
    structured outputs (validated against the requested schema)."""

    def __init__(self, text: str = "FAKE REPLY", structured_queue: list | None = None) -> None:
        self._text = text
        self._structured_queue = list(structured_queue or [])
        self.generate_calls: list[tuple[list[LLMMessage], str | None]] = []
        self.structured_calls: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return "fake"

    async def generate(
        self,
        messages: list[LLMMessage],
        *,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_output_tokens: int = 1024,
    ) -> LLMResponse:
        self.generate_calls.append((messages, system_prompt))
        return LLMResponse(text=self._text, provider=self.name, model="fake-model")

    async def generate_structured(
        self,
        messages: list[LLMMessage],
        schema: type[BaseModel],
        *,
        system_prompt: str | None = None,
        temperature: float = 0.0,
    ) -> BaseModel:
        self.structured_calls.append({
            "schema": schema.__name__,
            "system_prompt": system_prompt,
            "messages": messages,
        })
        if self._structured_queue:
            item = self._structured_queue.pop(0)
            if isinstance(item, Exception):
                raise item
            if isinstance(item, BaseModel):
                return item
            return schema.model_validate(item)
        # Sensible default: answer directly with the canned text.
        from app.agents.schemas import SupervisorDecision

        return SupervisorDecision(respond_directly=self._text)


class HashingEmbeddingProvider:
    """Deterministic embeddings for tests: CRC32 token hashing.

    Content words map (collision-light, 64 dims) to unit vectors; documents
    that share words score higher, unrelated words score ~0.
    """

    def __init__(self, dimensions: int = 64) -> None:
        self._dims = dimensions

    @property
    def name(self) -> str:
        return "fake-hash"

    @property
    def dimensions(self) -> int:
        return self._dims

    async def embed(self, texts):
        return [self._embed_one(t) for t in texts]

    _STOPWORDS = {
        "the", "a", "an", "in", "my", "of", "and", "or", "to", "is",
        "are", "what", "for", "on", "with", "that", "this", "it",
    }

    def _embed_one(self, text: str):
        import re
        import zlib

        vector = [0.0] * self._dims
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            if word in self._STOPWORDS:
                continue
            index = zlib.crc32(word.encode()) % self._dims
            vector[index] += 1.0
        norm = sum(v * v for v in vector) ** 0.5 or 1.0
        return [v / norm for v in vector]


class FakeTrace:
    """Lightweight trace stand-in when ToolTrace isn't needed."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def add_event(self, **kwargs) -> dict:
        event = {
            "agent": kwargs.get("agent", "?"),
            "action": kwargs.get("action", ""),
            "tool": kwargs.get("tool"),
            "status": kwargs.get("status", "ok"),
            "timestamp": "2026-01-01T00:00:00Z",
            "duration_ms": round(kwargs.get("duration_ms", 0.0), 1),
            "error": kwargs.get("error"),
        }
        self.events.append(event)
        return event
