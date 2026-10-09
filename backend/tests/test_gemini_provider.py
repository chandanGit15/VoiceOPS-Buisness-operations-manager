"""Tests for the Gemini provider boundary (no network calls)."""

import pytest

from app.core.config import get_settings
from app.services.llm import LLMNotConfiguredError, get_llm_provider
from app.services.llm.gemini_provider import GeminiProvider


@pytest.fixture()
def fresh_settings(monkeypatch):
    """Yield after clearing the cached Settings so env changes take effect,
    and clear it again afterwards to avoid leaking into other tests."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_gemini_provider_requires_api_key(monkeypatch, fresh_settings) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "")
    with pytest.raises(LLMNotConfiguredError):
        GeminiProvider()


def test_gemini_provider_is_an_llm_provider(monkeypatch, fresh_settings) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-123")
    provider = GeminiProvider()
    assert provider.name == "gemini"


def test_factory_rejects_unknown_provider(monkeypatch, fresh_settings) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "does-not-exist")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-123")
    with pytest.raises(LLMNotConfiguredError):
        get_llm_provider()
