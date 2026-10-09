"""ElevenLabs text-to-speech adapter (real REST API calls, key server-side)."""

from __future__ import annotations

import httpx

from app.core.config import get_settings
from app.services.voice.tts import SpeechResult, TTSError, TTSProvider

ELEVENLABS_URL_TMPL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"


class ElevenLabsTTS(TTSProvider):
    def __init__(self) -> None:
        settings = get_settings()
        if not settings.elevenlabs_api_key:
            from app.services.voice.tts import TTSNotConfiguredError

            raise TTSNotConfiguredError(
                "ELEVENLABS_API_KEY is not set; cannot use the ElevenLabs TTS provider."
            )
        self._api_key = settings.elevenlabs_api_key
        self._voice_id = settings.elevenlabs_voice_id
        self._model = settings.elevenlabs_tts_model

    @property
    def name(self) -> str:
        return "elevenlabs"

    async def synthesize(self, text: str) -> SpeechResult:
        headers = {
            "xi-api-key": self._api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }
        payload = {
            "text": text[:2500],  # provider limit; agent replies are short anyway
            "model_id": self._model,
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    ELEVENLABS_URL_TMPL.format(voice_id=self._voice_id),
                    headers=headers,
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise TTSError(f"ElevenLabs request failed: {type(exc).__name__}") from exc

        if response.status_code in (429, 500, 502, 503, 504):
            error = TTSError(f"ElevenLabs returned {response.status_code}")
            error.retryable = True
            raise error
        if response.status_code != 200:
            raise TTSError(f"ElevenLabs returned {response.status_code}")

        audio = response.content
        if not audio:
            raise TTSError("ElevenLabs returned empty audio.")

        return SpeechResult(audio=audio, content_type="audio/mpeg", provider=self.name)
