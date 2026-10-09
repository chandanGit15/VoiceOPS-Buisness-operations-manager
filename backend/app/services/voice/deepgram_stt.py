"""Deepgram speech-to-text adapter (real REST API calls, key server-side)."""

from __future__ import annotations

import httpx

from app.core.config import get_settings
from app.services.voice.stt import STTError, STTProvider, TranscriptionResult

DEEPGRAM_URL = "https://api.deepgram.com/v1/listen"


class DeepgramSTT(STTProvider):
    """Pre-recorded transcription via Deepgram's listen API.

    Audio is streamed straight through to the vendor and never persisted.
    Retryable HTTP failures (network, 429/5xx) are retried with backoff by
    the shared voice retry helper in the API layer; business errors are not.
    """

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.deepgram_api_key:
            from app.services.voice.stt import STTNotConfiguredError

            raise STTNotConfiguredError(
                "DEEPGRAM_API_KEY is not set; cannot use the Deepgram STT provider."
            )
        self._api_key = settings.deepgram_api_key
        self._model = settings.deepgram_stt_model

    @property
    def name(self) -> str:
        return "deepgram"

    async def transcribe(self, audio: bytes, content_type: str) -> TranscriptionResult:
        headers = {
            "Authorization": f"Token {self._api_key}",
            "Content-Type": content_type or "audio/webm",
        }
        params = {
            "model": self._model,
            "smart_format": "true",
            "punctuate": "true",
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    DEEPGRAM_URL, headers=headers, params=params, content=audio
                )
        except httpx.HTTPError as exc:
            raise STTError(f"Deepgram request failed: {type(exc).__name__}") from exc

        if response.status_code in (429, 500, 502, 503, 504):
            # Mark retryable so the API layer's voice retry helper can retry.
            error = STTError(f"Deepgram returned {response.status_code}")
            error.retryable = True
            raise error
        if response.status_code != 200:
            raise STTError(f"Deepgram returned {response.status_code}")

        data = response.json()
        try:
            channel = data["results"]["channels"][0]
            alternative = channel["alternatives"][0]
            text = (alternative.get("transcript") or "").strip()
        except (KeyError, IndexError) as exc:
            raise STTError("Deepgram response missing transcript") from exc

        if not text:
            raise STTError("No speech detected in the audio.")

        confidence = alternative.get("confidence")
        return TranscriptionResult(
            text=text,
            provider=self.name,
            confidence=float(confidence) if confidence is not None else None,
            raw_meta={"model": self._model},
        )
