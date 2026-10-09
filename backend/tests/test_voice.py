"""Tests for the voice layer: STT/TTS adapters and endpoints (no real keys)."""

import httpx
import pytest

from app.services.voice import (
    STTError,
    STTNotConfiguredError,
    TTSNotConfiguredError,
    get_stt_provider,
    get_tts_provider,
)
from app.services.voice.deepgram_stt import DeepgramSTT
from app.services.voice.elevenlabs_tts import ElevenLabsTTS

pytestmark = pytest.mark.asyncio


def _settings_cache_clear():
    from app.core.config import get_settings

    get_settings.cache_clear()


def _mock(module, handler):
    transport = httpx.MockTransport(handler)
    monkeypatch_target = module
    return transport, monkeypatch_target


# ---------- Provider construction ----------


async def test_stt_requires_api_key(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "")
    _settings_cache_clear()
    try:
        with pytest.raises(STTNotConfiguredError):
            DeepgramSTT()
    finally:
        _settings_cache_clear()


async def test_tts_requires_api_key(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "")
    _settings_cache_clear()
    try:
        with pytest.raises(TTSNotConfiguredError):
            ElevenLabsTTS()
    finally:
        _settings_cache_clear()


async def test_factory_rejects_unknown_providers(monkeypatch):
    monkeypatch.setenv("STT_PROVIDER", "nope")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "k")
    monkeypatch.setenv("TTS_PROVIDER", "nope")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    _settings_cache_clear()
    try:
        with pytest.raises(STTNotConfiguredError):
            get_stt_provider()
        with pytest.raises(TTSNotConfiguredError):
            get_tts_provider()
    finally:
        _settings_cache_clear()


# ---------- Deepgram STT adapter ----------


def _dg_client(monkeypatch, handler):
    from app.services.voice import deepgram_stt as module

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(module, "httpx", httpx)
    original_async_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(module.httpx, "AsyncClient", factory)


async def test_deepgram_transcribe_parses_transcript(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["model"] == "nova-2"
        return httpx.Response(200, json={
            "results": {"channels": [{"alternatives": [{"transcript": "  what's the weather in Bangalore tomorrow  ", "confidence": 0.94}]}]}
        })

    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")
    _settings_cache_clear()
    try:
        _dg_client(monkeypatch, handler)
        provider = DeepgramSTT()
        result = await provider.transcribe(b"fake-audio", "audio/webm")
        assert result.text == "what's the weather in Bangalore tomorrow"
        assert result.provider == "deepgram"
        assert result.confidence == pytest.approx(0.94)
    finally:
        _settings_cache_clear()


async def test_deepgram_no_speech_detected(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "results": {"channels": [{"alternatives": [{"transcript": ""}]}]}
        })

    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")
    _settings_cache_clear()
    try:
        _dg_client(monkeypatch, handler)
        with pytest.raises(STTError, match="No speech detected"):
            await DeepgramSTT().transcribe(b"fake", "audio/webm")
    finally:
        _settings_cache_clear()


# ---------- ElevenLabs TTS adapter ----------


def _el_client(monkeypatch, handler):
    from app.services.voice import elevenlabs_tts as module

    transport = httpx.MockTransport(handler)
    original_async_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(module.httpx, "AsyncClient", factory)


async def test_elevenlabs_synthesize_returns_audio(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert "xi-api-key" in request.headers
        return httpx.Response(200, content=b"ID3fake-mp3-bytes")

    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    _settings_cache_clear()
    try:
        _el_client(monkeypatch, handler)
        result = await ElevenLabsTTS().synthesize("Hello there")
        assert result.audio == b"ID3fake-mp3-bytes"
        assert result.content_type == "audio/mpeg"
        assert result.provider == "elevenlabs"
    finally:
        _settings_cache_clear()


# ---------- Endpoints ----------


async def test_transcribe_rejects_non_audio(patched_db, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=patched_db), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/voice/transcribe",
            files={"audio": ("notes.txt", b"hello world", "text/plain")},
        )
        assert response.status_code == 415
        assert response.json()["error"]["code"] == "unsupported_media_type"


async def test_transcribe_rejects_empty_audio(patched_db, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=patched_db), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/voice/transcribe",
            files={"audio": ("clip.webm", b"", "audio/webm")},
        )
        assert response.status_code == 422


async def test_transcribe_graceful_when_unconfigured(patched_db, monkeypatch):
    """No Deepgram key → clean 503 envelope, not a crash."""
    from httpx import ASGITransport, AsyncClient

    monkeypatch.setenv("DEEPGRAM_API_KEY", "")
    _settings_cache_clear()
    try:
        async with AsyncClient(transport=ASGITransport(app=patched_db), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/voice/transcribe",
                files={"audio": ("clip.webm", b"audio-bytes", "audio/webm")},
            )
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "stt_not_configured"
    finally:
        _settings_cache_clear()


async def test_transcribe_happy_path_with_mocked_provider(patched_db, monkeypatch):
    """Endpoint → provider wiring works end to end with a stubbed vendor call."""
    from httpx import ASGITransport, AsyncClient

    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")
    _settings_cache_clear()
    try:
        class StubProvider:
            name = "stub"

            async def transcribe(self, audio, content_type):
                return type("R", (), {"text": "create a task to study operating systems",
                                      "provider": "stub", "confidence": 0.9, "raw_meta": {}})()

        import app.api.voice as voice_module

        monkeypatch.setattr(voice_module, "get_stt_provider", lambda: StubProvider())

        async with AsyncClient(transport=ASGITransport(app=patched_db), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/voice/transcribe",
                files={"audio": ("clip.webm", b"audio-bytes", "audio/webm")},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["text"] == "create a task to study operating systems"
            assert body["provider"] == "stub"
    finally:
        _settings_cache_clear()


async def test_speak_graceful_when_unconfigured(patched_db, monkeypatch):
    """No ElevenLabs key → clean 503 envelope, not a crash."""
    from httpx import ASGITransport, AsyncClient

    monkeypatch.setenv("ELEVENLABS_API_KEY", "")
    _settings_cache_clear()
    try:
        async with AsyncClient(transport=ASGITransport(app=patched_db), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/voice/speak", json={"text": "Rain expected tomorrow."}
            )
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "tts_not_configured"
    finally:
        _settings_cache_clear()


async def test_speak_happy_path_returns_audio(patched_db, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    class StubTTS:
        name = "stub"

        async def synthesize(self, text):
            return type("S", (), {"audio": b"mp3-bytes", "content_type": "audio/mpeg", "provider": "stub"})()

    import app.api.voice as voice_module

    monkeypatch.setattr(voice_module, "get_tts_provider", lambda: StubTTS())

    async with AsyncClient(transport=ASGITransport(app=patched_db), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/voice/speak", json={"text": "Rain expected tomorrow."}
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("audio/mpeg")
        assert response.content == b"mp3-bytes"


async def test_speak_validates_empty_text(patched_db):
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=patched_db), base_url="http://test") as client:
        response = await client.post("/api/v1/voice/speak", json={"text": ""})
        assert response.status_code == 422
