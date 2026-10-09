"""Voice endpoints (Part 5): speech-to-text and text-to-speech.

Both endpoints keep provider keys server-side: the frontend uploads audio /
receives audio, never touching vendor APIs. Voice requests continue through
the existing /api/v1/chat endpoint, so voice and text share one agent system.
"""

import asyncio
import logging
import time

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from app.core.rate_limit import rate_limit
from app.core.security import get_current_user
from app.models import User
from app.services.voice import (
    STTError,
    STTNotConfiguredError,
    TTSError,
    TTSNotConfiguredError,
    get_stt_provider,
    get_tts_provider,
)

logger = logging.getLogger("app.api.voice")

router = APIRouter(
    dependencies=[Depends(rate_limit(window_seconds=60, per_minute_setting="voice_rate_limit_per_min"))],
)

MAX_AUDIO_BYTES = 15 * 1024 * 1024  # 15 MB cap (~minutes of compressed audio)
ALLOWED_AUDIO_PREFIXES = ("audio/", "application/ogg")

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    from app.core.errors import error_payload

    return JSONResponse(status_code=status_code, content=error_payload(code, message))


async def _vendor_call_with_retry(operation, *, max_attempts: int = 3):
    """Retry vendor calls whose error is marked retryable (network, 429/5xx)."""
    for attempt in range(max_attempts):
        try:
            return await operation()
        except (STTError, TTSError) as exc:
            retryable = getattr(exc, "retryable", False)
            if not retryable or attempt == max_attempts - 1:
                raise
            logger.warning(
                "voice vendor call failed (attempt %d/%d): %s — retrying",
                attempt + 1, max_attempts, exc,
            )
            await asyncio.sleep(1.0 * (attempt + 1))


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2500)


@router.post(
    "/voice/transcribe",
    summary="Transcribe an audio recording to text",
    tags=["voice"],
)
async def transcribe(
    audio: UploadFile = File(...),
    session_id: str | None = Form(default=None, max_length=64),
    _: User = Depends(get_current_user),
) -> JSONResponse:
    """Accept a recorded audio clip and return its transcript.

    The transcript is returned to the frontend, which then sends it to
    /api/v1/chat — keeping voice and text on the same agent pipeline.
    """
    started = time.perf_counter()

    content_type = audio.content_type or ""
    if content_type and not content_type.startswith(ALLOWED_AUDIO_PREFIXES):
        return _error_response(415, "unsupported_media_type",
                               f"Unsupported audio type: {content_type}")

    audio_bytes = await audio.read()
    if not audio_bytes:
        return _error_response(422, "validation_error", "Audio file is empty.")
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        return _error_response(413, "payload_too_large", "Audio file exceeds 15 MB.")

    try:
        provider = get_stt_provider()
    except (STTNotConfiguredError, STTError) as exc:
        return _error_response(exc.status_code, exc.code, exc.message)

    try:
        result = await _vendor_call_with_retry(
            lambda: provider.transcribe(audio_bytes, content_type)
        )
    except (STTNotConfiguredError, STTError) as exc:
        return _error_response(exc.status_code, exc.code, exc.message)

    logger.info(
        "transcribed %.1f KB via %s in %.0f ms",
        len(audio_bytes) / 1024, result.provider, (time.perf_counter() - started) * 1000,
    )
    return JSONResponse(status_code=200, content={
        "text": result.text,
        "provider": result.provider,
        "confidence": result.confidence,
        "session_id": session_id,
    })


@router.post(
    "/voice/speak",
    summary="Synthesize speech audio from text",
    tags=["voice"],
    responses={200: {"content": {"audio/mpeg": {}}}},
)
async def speak(
    payload: SpeakRequest,
    _: User = Depends(get_current_user),
) -> Response:
    """Convert an assistant reply to spoken audio (mp3).

    Returns raw audio bytes with the correct content type; the frontend
    plays them via an Audio element. No key ever reaches the client.
    """
    try:
        provider = get_tts_provider()
    except (TTSNotConfiguredError, TTSError) as exc:
        return _error_response(exc.status_code, exc.code, exc.message)

    try:
        result = await _vendor_call_with_retry(lambda: provider.synthesize(payload.text))
    except (TTSNotConfiguredError, TTSError) as exc:
        return _error_response(exc.status_code, exc.code, exc.message)

    return Response(content=result.audio, media_type=result.content_type)
