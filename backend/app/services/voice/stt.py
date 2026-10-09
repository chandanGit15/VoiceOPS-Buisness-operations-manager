"""
Speech-to-text provider abstraction.

Voice input arrives as an uploaded audio file (webm/opus from the browser
MediaRecorder, wav, mp3, ...). Providers implement transcribe() and return
a plain TranscriptionResult. The concrete provider is selected by the
STT_PROVIDER environment variable; adding a vendor = one adapter class.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.core.config import get_settings
from app.core.errors import APIError

logger = logging.getLogger("app.voice")


class STTError(APIError):
    status_code = 502
    code = "stt_error"
    message = "Speech-to-text failed."


class STTNotConfiguredError(STTError):
    status_code = 503
    code = "stt_not_configured"
    message = "No speech-to-text provider is configured."


@dataclass
class TranscriptionResult:
    text: str
    provider: str
    confidence: float | None = None
    raw_meta: dict = field(default_factory=dict)


class STTProvider(ABC):
    """Interface every speech-to-text backend must implement."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    async def transcribe(self, audio: bytes, content_type: str) -> TranscriptionResult:
        """Transcribe raw audio bytes to text."""


def get_stt_provider() -> STTProvider:
    """Return the STT provider selected by the STT_PROVIDER env var.

    Lazy vendor imports keep optional SDKs optional, mirroring the LLM layer.
    """
    settings = get_settings()
    provider_name = settings.stt_provider.lower()

    if provider_name == "deepgram":
        from app.services.voice.deepgram_stt import DeepgramSTT

        return DeepgramSTT()

    raise STTNotConfiguredError(
        f"Unknown STT_PROVIDER '{settings.stt_provider}'. Supported: 'deepgram'."
    )
