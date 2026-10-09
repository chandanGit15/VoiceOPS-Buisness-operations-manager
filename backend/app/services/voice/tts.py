"""
Text-to-speech provider abstraction.

Providers implement synthesize() and return audio bytes (mp3 by default).
The concrete provider is selected by the TTS_PROVIDER environment variable.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.core.config import get_settings
from app.core.errors import APIError

logger = logging.getLogger("app.voice")


class TTSError(APIError):
    status_code = 502
    code = "tts_error"
    message = "Text-to-speech failed."


class TTSNotConfiguredError(TTSError):
    status_code = 503
    code = "tts_not_configured"
    message = "No text-to-speech provider is configured."


@dataclass
class SpeechResult:
    audio: bytes
    content_type: str  # e.g. "audio/mpeg"
    provider: str


class TTSProvider(ABC):
    """Interface every text-to-speech backend must implement."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    async def synthesize(self, text: str) -> SpeechResult:
        """Convert text to synthesized speech audio bytes."""


def get_tts_provider() -> TTSProvider:
    settings = get_settings()
    provider_name = settings.tts_provider.lower()

    if provider_name == "elevenlabs":
        from app.services.voice.elevenlabs_tts import ElevenLabsTTS

        return ElevenLabsTTS()

    raise TTSNotConfiguredError(
        f"Unknown TTS_PROVIDER '{settings.tts_provider}'. Supported: 'elevenlabs'."
    )
