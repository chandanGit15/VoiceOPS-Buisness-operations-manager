"""Voice provider layer: STT and TTS behind swappable abstractions."""

from app.services.voice.stt import (
    STTError,
    STTNotConfiguredError,
    STTProvider,
    TranscriptionResult,
    get_stt_provider,
)
from app.services.voice.tts import (
    SpeechResult,
    TTSError,
    TTSNotConfiguredError,
    TTSProvider,
    get_tts_provider,
)

__all__ = [
    "STTError",
    "STTNotConfiguredError",
    "STTProvider",
    "TranscriptionResult",
    "get_stt_provider",
    "SpeechResult",
    "TTSError",
    "TTSNotConfiguredError",
    "TTSProvider",
    "get_tts_provider",
]
