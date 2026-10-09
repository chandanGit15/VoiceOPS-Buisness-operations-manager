"""Google Gemini implementation of the LLMProvider interface."""

from __future__ import annotations

import logging

from google import genai
from google.genai import types as genai_types
from pydantic import BaseModel, ValidationError

from app.core.config import get_settings
from app.core.errors import APIError
from app.services.llm.base import (
    LLMError,
    LLMMessage,
    LLMNotConfiguredError,
    LLMProvider,
    LLMResponse,
)

logger = logging.getLogger("app.llm.gemini")


class GeminiProvider(LLMProvider):
    """Async Gemini client using the official google-genai SDK."""

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.gemini_api_key:
            raise LLMNotConfiguredError(
                "GEMINI_API_KEY is not set; cannot use the Gemini provider."
            )
        self._model = settings.gemini_model
        # The SDK reads the key from the constructor argument only — never
        # from anything that could reach the frontend.
        self._client = genai.Client(api_key=settings.gemini_api_key)

    @property
    def name(self) -> str:
        return "gemini"

    async def generate(
        self,
        messages: list[LLMMessage],
        *,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_output_tokens: int = 1024,
    ) -> LLMResponse:
        # Gemini is conversation-oriented: a system prompt is passed via
        # config, history as alternating user/model content.
        contents = [
            genai_types.Content(
                role=m.role,  # "user" or "model" map 1:1 to Gemini roles
                parts=[genai_types.Part(text=m.content)],
            )
            for m in messages
        ]

        config = genai_types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )

        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=contents,
                config=config,
            )
        except APIError:
            raise
        except Exception as exc:  # narrow SDK exceptions to our error envelope
            logger.exception("Gemini generate_content failed")
            raise LLMError(f"Gemini request failed: {type(exc).__name__}") from exc

        text = (response.text or "").strip()
        if not text:
            # Typically a blocked/safety-filtered or empty completion.
            logger.warning("Gemini returned an empty response")
            raise LLMError("Gemini returned an empty response.")

        usage = None
        if response.usage_metadata is not None:
            usage = {
                "input_tokens": response.usage_metadata.prompt_token_count,
                "output_tokens": response.usage_metadata.candidates_token_count,
                "total_tokens": response.usage_metadata.total_token_count,
            }

        return LLMResponse(text=text, provider=self.name, model=self._model, usage_metadata=usage)

    async def generate_structured(
        self,
        messages: list[LLMMessage],
        schema: type[BaseModel],
        *,
        system_prompt: str | None = None,
        temperature: float = 0.0,
    ) -> BaseModel:
        """Ask Gemini for output constrained to the given Pydantic schema.

        Uses the SDK's native structured-output support; a parse/validation
        failure is normalized to LLMError (with retry handled by callers).
        """
        contents = [
            genai_types.Content(
                role=m.role,
                parts=[genai_types.Part(text=m.content)],
            )
            for m in messages
        ]
        config = genai_types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=temperature,
            response_mime_type="application/json",
            response_schema=schema,
        )

        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=contents,
                config=config,
            )
        except APIError:
            raise
        except Exception as exc:
            logger.exception("Gemini structured generation failed")
            raise LLMError(f"Gemini request failed: {type(exc).__name__}") from exc

        parsed = response.parsed  # SDK validates against response_schema
        if parsed is None:
            raise LLMError("Gemini returned output that did not match the required schema.")
        if isinstance(parsed, schema):
            return parsed
        # The SDK may return a compatible generic structure; coerce via Pydantic.
        try:
            return schema.model_validate(parsed)
        except ValidationError as exc:
            raise LLMError("Gemini returned output that did not match the required schema.") from exc
