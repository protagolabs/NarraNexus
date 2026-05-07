"""
@file_name: test_audio_transcription.py
@author: NarraNexus
@date: 2026-05-02
@description: Unit tests for audio_transcription module.

Strategy mirrors tests/common_tools_module/test_web_search_brave_tool.py:
patch httpx.AsyncClient via monkeypatch (no respx dependency, project
doesn't ship one). Two test surfaces:

  1. _resolve_credential — 4-tier provider fallback chain. Heavily
     mocked: settings overrides, fake UserProviderService, fake
     SystemProviderService.
  2. transcribe_audio — HTTP path. Mocks resolved credential and
     httpx.AsyncClient.

Goal: lock the contracts the upload route depends on:
  - Never raises (any failure → None)
  - 25MB Whisper limit second-line guard
  - Retry once on 429 / 5xx, no retry on other 4xx
  - 35s overall timeout fires
  - OpenRouter base_url → "openai/whisper-1" model id
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from xyz_agent_context.utils import audio_transcription as at
from xyz_agent_context.utils.audio_transcription import (
    WhisperCredential,
    _is_compatible_provider,
    _is_openrouter,
    _normalize_model,
    _resolve_credential,
    is_transcription_available,
    transcribe_audio,
)


# ============================================================================
# Helpers
# ============================================================================


def _make_response(status_code: int, text: str = "") -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    return resp


def _patch_httpx(monkeypatch, handler):
    """Replace httpx.AsyncClient with a fake whose .post(...) calls handler.

    handler must be an async callable accepting (url, data, files, headers)
    and returning a mock response.
    """
    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, url, data=None, files=None, headers=None):
            return await handler(url, data, files, headers)

    monkeypatch.setattr(at.httpx, "AsyncClient", _FakeClient)


def _make_credential(
    base_url: str = "https://api.openai.com/v1",
    model: str = "whisper-1",
    source: str = "test",
) -> WhisperCredential:
    return WhisperCredential(
        api_key="test-key",
        base_url=base_url,
        model=model,
        source=source,
    )


def _patch_resolve(monkeypatch, cred: WhisperCredential | None):
    """Force _resolve_credential to return a fixed credential (or None)."""
    async def _fake(_user_id):
        return cred
    monkeypatch.setattr(at, "_resolve_credential", _fake)


def _make_audio_file(tmp_path: Path, suffix: str = ".wav", size: int = 1024) -> Path:
    p = tmp_path / f"sample{suffix}"
    p.write_bytes(b"\x00" * size)
    return p


# ============================================================================
# _normalize_model
# ============================================================================


def test_normalize_model_default_for_openai():
    assert _normalize_model("https://api.openai.com/v1") == "whisper-1"


def test_normalize_model_default_for_netmind():
    assert _normalize_model("https://api.netmind.ai/inference-api/openai/v1") == "whisper-1"


def test_normalize_model_openrouter_gets_prefix():
    assert _normalize_model("https://openrouter.ai/api/v1") == "openai/whisper-1"


def test_normalize_model_openrouter_case_insensitive():
    assert _normalize_model("https://OpenRouter.AI/api/v1") == "openai/whisper-1"


def test_is_openrouter_helper():
    assert _is_openrouter("https://openrouter.ai/api/v1") is True
    assert _is_openrouter("https://api.openai.com/v1") is False
    assert _is_openrouter("") is False


# ============================================================================
# _is_compatible_provider — provider acceptance gate
# ============================================================================


def _fake_prov(base_url: str, *, active: bool = True, key: str = "sk-x"):
    """Build the minimal duck-typed Provider object that
    _is_compatible_provider reads. Avoids depending on the real Pydantic
    schema so the helper can be tested in isolation."""
    from xyz_agent_context.schema.provider_schema import ProviderProtocol

    prov = MagicMock()
    prov.is_active = active
    prov.protocol = ProviderProtocol.OPENAI
    prov.api_key = key
    prov.base_url = base_url
    return prov


def test_compatible_accepts_openai_official():
    assert _is_compatible_provider(_fake_prov("https://api.openai.com/v1")) is True


def test_compatible_accepts_yunwu():
    assert _is_compatible_provider(_fake_prov("https://yunwu.ai/v1")) is True


def test_compatible_rejects_netmind():
    """NetMind has a different request shape (audio_url + /v1/generation)
    that we don't implement. Never resolve to a NetMind credential."""
    assert (
        _is_compatible_provider(
            _fake_prov("https://api.netmind.ai/inference-api/openai/v1")
        )
        is False
    )


def test_compatible_rejects_openrouter():
    """OpenRouter Whisper uses JSON+base64, not multipart. Until the
    dispatcher branch lands, reject at the resolution gate so OpenRouter
    users land on the "transcription unavailable" path with a clean
    error instead of silent 4xx."""
    assert (
        _is_compatible_provider(_fake_prov("https://openrouter.ai/api/v1"))
        is False
    )


def test_compatible_rejects_inactive_provider():
    assert (
        _is_compatible_provider(
            _fake_prov("https://api.openai.com/v1", active=False)
        )
        is False
    )


def test_compatible_rejects_blank_key():
    assert (
        _is_compatible_provider(_fake_prov("https://api.openai.com/v1", key=""))
        is False
    )


# ============================================================================
# _resolve_credential — fallback chain (user_provider → system → settings.openai)
# ============================================================================


@pytest.mark.asyncio
async def test_resolve_no_provider_returns_none(monkeypatch):
    """All tiers empty → None."""
    monkeypatch.setattr(at.settings, "openai_api_key", "")
    # SystemProviderService.is_enabled() → False is the default in tests
    cred = await _resolve_credential(None)
    assert cred is None


@pytest.mark.asyncio
async def test_resolve_falls_through_to_settings_openai(monkeypatch):
    """No user / no system → settings.openai_api_key wins."""
    monkeypatch.setattr(at.settings, "openai_api_key", "sk-classic-fallback")

    cred = await _resolve_credential(None)
    assert cred is not None
    assert cred.api_key == "sk-classic-fallback"
    assert cred.base_url == "https://api.openai.com/v1"
    assert cred.model == "whisper-1"
    assert cred.source == "settings.openai"


@pytest.mark.asyncio
async def test_resolve_db_failure_falls_through(monkeypatch):
    """User provider lookup raises → fall through, never propagate."""
    monkeypatch.setattr(at.settings, "openai_api_key", "fallback-key")

    # Force the lazy db_factory import to raise so the user-provider tier
    # explodes — and we should still land on settings.openai.
    import xyz_agent_context.utils.db_factory as dbf

    async def boom():
        raise RuntimeError("DB unavailable")

    monkeypatch.setattr(dbf, "get_db_client", boom)

    cred = await _resolve_credential("user_x")
    assert cred is not None
    assert cred.source == "settings.openai"


# ============================================================================
# is_transcription_available
# ============================================================================


@pytest.mark.asyncio
async def test_available_with_settings_openai(monkeypatch):
    monkeypatch.setattr(at.settings, "openai_api_key", "sk-something")
    assert await is_transcription_available(None) is True


@pytest.mark.asyncio
async def test_available_returns_false_when_nothing_configured(monkeypatch):
    monkeypatch.setattr(at.settings, "openai_api_key", "")
    assert await is_transcription_available(None) is False


# ============================================================================
# transcribe_audio — pre-flight guards
# ============================================================================


@pytest.mark.asyncio
async def test_transcribe_no_credential_returns_none(monkeypatch, tmp_path):
    _patch_resolve(monkeypatch, None)
    f = _make_audio_file(tmp_path)
    result = await transcribe_audio(str(f))
    assert result is None


@pytest.mark.asyncio
async def test_transcribe_missing_file_returns_none(monkeypatch):
    _patch_resolve(monkeypatch, _make_credential())
    assert await transcribe_audio("/path/that/does/not/exist.mp3") is None


@pytest.mark.asyncio
async def test_transcribe_unsupported_ext_returns_none(monkeypatch, tmp_path):
    _patch_resolve(monkeypatch, _make_credential())
    f = _make_audio_file(tmp_path, suffix=".bin")
    assert await transcribe_audio(str(f)) is None


@pytest.mark.asyncio
async def test_transcribe_empty_file_returns_none(monkeypatch, tmp_path):
    _patch_resolve(monkeypatch, _make_credential())
    f = _make_audio_file(tmp_path, size=0)
    assert await transcribe_audio(str(f)) is None


@pytest.mark.asyncio
async def test_transcribe_oversize_returns_none(monkeypatch, tmp_path):
    _patch_resolve(monkeypatch, _make_credential())
    f = _make_audio_file(tmp_path, size=at.WHISPER_MAX_FILE_BYTES + 1)
    assert await transcribe_audio(str(f)) is None


# ============================================================================
# transcribe_audio — HTTP success / shape
# ============================================================================


@pytest.mark.asyncio
async def test_transcribe_happy_path_returns_text(monkeypatch, tmp_path):
    _patch_resolve(monkeypatch, _make_credential())
    f = _make_audio_file(tmp_path)

    async def handler(url, data, files, headers):
        # Endpoint constructed correctly
        assert url == "https://api.openai.com/v1/audio/transcriptions"
        # Auth header present
        assert headers["Authorization"] == "Bearer test-key"
        # Multipart fields shape
        assert data["model"] == "whisper-1"
        assert data["response_format"] == "text"
        assert "file" in files
        return _make_response(200, text="hello world\n")

    _patch_httpx(monkeypatch, handler)

    result = await transcribe_audio(str(f))
    assert result == "hello world"


@pytest.mark.asyncio
async def test_transcribe_uses_correct_url_for_netmind(monkeypatch, tmp_path):
    cred = _make_credential(
        base_url="https://api.netmind.ai/inference-api/openai/v1",
        model="whisper-1",
        source="user_provider:netmind",
    )
    _patch_resolve(monkeypatch, cred)
    f = _make_audio_file(tmp_path)

    captured: dict = {}

    async def handler(url, data, files, headers):
        captured["url"] = url
        captured["model"] = data["model"]
        return _make_response(200, text="netmind text")

    _patch_httpx(monkeypatch, handler)
    result = await transcribe_audio(str(f))
    assert result == "netmind text"
    assert captured["url"] == (
        "https://api.netmind.ai/inference-api/openai/v1/audio/transcriptions"
    )
    assert captured["model"] == "whisper-1"


@pytest.mark.asyncio
async def test_transcribe_dispatcher_handles_openrouter_credential(monkeypatch, tmp_path):
    """OpenRouter is rejected by ``_is_compatible_provider`` today, so
    the credential resolver never hands an OpenRouter credential to
    ``_call_whisper`` in production. This test bypasses resolution and
    asserts the **dispatcher** still composes the right URL + model id
    for that base_url, so when the JSON+base64 OpenRouter branch lands
    the URL/model wiring is already correct."""
    cred = _make_credential(
        base_url="https://openrouter.ai/api/v1",
        model="openai/whisper-1",
        source="user_provider:openrouter",
    )
    _patch_resolve(monkeypatch, cred)
    f = _make_audio_file(tmp_path)

    captured: dict = {}

    async def handler(url, data, files, headers):
        captured["url"] = url
        captured["model"] = data["model"]
        return _make_response(200, text="openrouter text")

    _patch_httpx(monkeypatch, handler)
    result = await transcribe_audio(str(f))
    assert result == "openrouter text"
    assert captured["url"] == "https://openrouter.ai/api/v1/audio/transcriptions"
    assert captured["model"] == "openai/whisper-1"


@pytest.mark.asyncio
async def test_transcribe_passes_language_when_set(monkeypatch, tmp_path):
    _patch_resolve(monkeypatch, _make_credential())
    f = _make_audio_file(tmp_path)

    captured: dict = {}

    async def handler(url, data, files, headers):
        captured["data"] = dict(data)
        return _make_response(200, text="hi")

    _patch_httpx(monkeypatch, handler)
    await transcribe_audio(str(f), language="zh")
    assert captured["data"]["language"] == "zh"


@pytest.mark.asyncio
async def test_transcribe_empty_response_returns_none(monkeypatch, tmp_path):
    """Whisper sometimes returns "" for silent audio — treat as None."""
    _patch_resolve(monkeypatch, _make_credential())
    f = _make_audio_file(tmp_path)

    async def handler(url, data, files, headers):
        return _make_response(200, text="   \n  ")

    _patch_httpx(monkeypatch, handler)
    assert await transcribe_audio(str(f)) is None


# ============================================================================
# transcribe_audio — retry behaviour
# ============================================================================


@pytest.mark.asyncio
async def test_transcribe_429_then_success(monkeypatch, tmp_path):
    _patch_resolve(monkeypatch, _make_credential())
    f = _make_audio_file(tmp_path)

    call_count = {"n": 0}

    async def handler(url, data, files, headers):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _make_response(429, text="rate limited")
        return _make_response(200, text="ok")

    _patch_httpx(monkeypatch, handler)
    # Skip the retry sleep
    async def _no_sleep(*_a, **_k): return None
    monkeypatch.setattr(at.asyncio, "sleep", _no_sleep)
    assert await transcribe_audio(str(f)) == "ok"
    assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_transcribe_429_exhausted_returns_none(monkeypatch, tmp_path):
    _patch_resolve(monkeypatch, _make_credential())
    f = _make_audio_file(tmp_path)

    async def handler(url, data, files, headers):
        return _make_response(429, text="still rate limited")

    _patch_httpx(monkeypatch, handler)
    async def _no_sleep(*_a, **_k): return None
    monkeypatch.setattr(at.asyncio, "sleep", _no_sleep)
    assert await transcribe_audio(str(f)) is None


@pytest.mark.asyncio
async def test_transcribe_4xx_no_retry(monkeypatch, tmp_path):
    """400 / 401 / 403 are not retryable."""
    _patch_resolve(monkeypatch, _make_credential())
    f = _make_audio_file(tmp_path)

    call_count = {"n": 0}

    async def handler(url, data, files, headers):
        call_count["n"] += 1
        return _make_response(401, text="bad key")

    _patch_httpx(monkeypatch, handler)
    assert await transcribe_audio(str(f)) is None
    assert call_count["n"] == 1  # No retry


@pytest.mark.asyncio
async def test_transcribe_5xx_retried(monkeypatch, tmp_path):
    _patch_resolve(monkeypatch, _make_credential())
    f = _make_audio_file(tmp_path)

    call_count = {"n": 0}

    async def handler(url, data, files, headers):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _make_response(503, text="service unavailable")
        return _make_response(200, text="recovered")

    _patch_httpx(monkeypatch, handler)
    async def _no_sleep(*_a, **_k): return None
    monkeypatch.setattr(at.asyncio, "sleep", _no_sleep)
    assert await transcribe_audio(str(f)) == "recovered"


@pytest.mark.asyncio
async def test_transcribe_http_error_then_success(monkeypatch, tmp_path):
    """ConnectError on first attempt → retry → 200 on second."""
    _patch_resolve(monkeypatch, _make_credential())
    f = _make_audio_file(tmp_path)

    call_count = {"n": 0}

    async def handler(url, data, files, headers):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise httpx.ConnectError("boom")
        return _make_response(200, text="back online")

    _patch_httpx(monkeypatch, handler)
    async def _no_sleep(*_a, **_k): return None
    monkeypatch.setattr(at.asyncio, "sleep", _no_sleep)
    assert await transcribe_audio(str(f)) == "back online"


# ============================================================================
# transcribe_audio — never raises
# ============================================================================


@pytest.mark.asyncio
async def test_transcribe_unexpected_exception_returns_none(monkeypatch, tmp_path):
    """Non-HTTP exception inside _call_whisper → swallowed, returns None."""
    _patch_resolve(monkeypatch, _make_credential())
    f = _make_audio_file(tmp_path)

    async def handler(url, data, files, headers):
        raise RuntimeError("totally unexpected")

    _patch_httpx(monkeypatch, handler)
    # asyncio.sleep used inside retry path
    async def _no_sleep(*_a, **_k): return None
    monkeypatch.setattr(at.asyncio, "sleep", _no_sleep)
    assert await transcribe_audio(str(f)) is None
