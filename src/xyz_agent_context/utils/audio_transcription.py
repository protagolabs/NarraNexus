"""
@file_name: audio_transcription.py
@author: NarraNexus
@date: 2026-05-02
@description: Whisper-API audio transcription utility for chat attachments

Stateless module called from the attachment upload route to populate
``Attachment.transcript`` for ``audio/*`` MIME types.

Contract: never raises. Any failure (no provider, network error, oversize
file, bad credentials, etc.) returns ``None``. The upload path treats
transcription as best-effort enrichment — losing the transcript must not
lose the upload itself.

Provider strategy
-----------------
Reuses NarraNexus's existing OpenAI-protocol provider system. The
multipart Whisper contract at ``/audio/transcriptions`` is the only
shape currently dispatched, so only providers that speak that exact
shape are accepted today: **OpenAI official** and **Yunwu** (plus any
self-hosted whisper.cpp behind an OpenAI-shaped endpoint).

Resolution order in :func:`_resolve_credential`:

1. ``UserProviderService.get_user_config(user_id)`` — official OpenAI
   first (base_url contains ``api.openai.com``), preferred for quality
   and contract stability.
2. Same source — any other compatible provider (Yunwu / self-hosted).
3. ``SystemProviderService.get_config()`` openai-protocol provider
   (cloud free-tier).
4. ``settings.openai_api_key`` + ``api.openai.com/v1`` (local .env classic).
5. ``None`` → silent degrade, ``transcript = null`` on the response.

Skipped at every tier (will return ``None`` even when configured):

- **NetMind** — Whisper exposed at ``/v1/generation`` with a
  JSON+``audio_url`` contract that requires public URL hosting we don't
  have.
- **OpenRouter** — Whisper exposed at the same path but with a
  JSON+base64 body, not multipart. The dispatcher branch is planned
  for a later phase; for now :func:`_is_compatible_provider` rejects
  OpenRouter so users with only an OpenRouter key get a clean
  "transcription unavailable" hint instead of silent 4xx failures.

Three-layer timeout (mirrors ``web_search_brave_tool.py`` defense)
------------------------------------------------------------------
1. ``httpx.Timeout`` per-call
2. ``asyncio.wait_for`` around the entire ``transcribe_audio()`` call
3. The upload route does NOT add an outer wait_for — FastAPI workers
   must return within tens of seconds and that's already covered by
   ``_OVERALL_TIMEOUT_S``.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx
from loguru import logger

from xyz_agent_context.settings import settings
from xyz_agent_context.schema.provider_schema import ProviderProtocol


SUPPORTED_AUDIO_EXTENSIONS = frozenset({
    ".mp3", ".mp4", ".mpeg", ".mpga", ".m4a",
    ".wav", ".webm", ".ogg", ".oga", ".opus",
    ".flac", ".amr",
})

# Whisper hard limit; second-line guard because backend max_upload_bytes
# default (50MB) is larger than what Whisper accepts.
WHISPER_MAX_FILE_BYTES = 25 * 1024 * 1024

_HTTPX_TIMEOUT = httpx.Timeout(connect=3.0, read=30.0, write=10.0, pool=3.0)
_OVERALL_TIMEOUT_S: float = 35.0
_MAX_ATTEMPTS = 2  # one initial + one retry on 429/5xx


@dataclass(frozen=True)
class WhisperCredential:
    """Resolved transcription provider — base_url + api_key + model.

    ``source`` is a free-form tag used only for logs (e.g.
    "user_provider:user:openai", "user_provider:yunwu", "system_default",
    "settings.openai") so operators can tell which fallback tier
    produced this credential.
    """
    api_key: str
    base_url: str
    model: str
    source: str


def _is_openrouter(base_url: str) -> bool:
    return "openrouter.ai" in (base_url or "").lower()


def _is_netmind(base_url: str) -> bool:
    """NetMind exposes Whisper through its own ``/v1/generation`` endpoint
    with a JSON+audio_url request shape, not the OpenAI multipart contract.
    Skipped during credential resolution; integrating would require an
    audio-hosting layer (presigned S3 / public-reachable URL) that is out
    of scope for Phase 1."""
    return "netmind.ai" in (base_url or "").lower()


def _is_official_openai(base_url: str) -> bool:
    """Official OpenAI endpoint — preferred over compatible aggregators
    when both are configured. Highest quality + most standard contract,
    so it's the safest default."""
    return "api.openai.com" in (base_url or "").lower()


def _is_compatible_provider(prov) -> bool:
    """A user_provider is usable for transcription iff it speaks the
    OpenAI Whisper multipart contract at ``/audio/transcriptions``.

    Currently supported: OpenAI official + Yunwu (and any self-hosted
    whisper.cpp behind an OpenAI-shaped endpoint). NetMind and OpenRouter
    are skipped here:

    - **NetMind** uses a different ``/v1/generation`` + ``audio_url``
      shape that needs public URL hosting we don't have.
    - **OpenRouter** uses JSON + base64 at the same path; supported
      upstream but not in the multipart dispatcher below. Will be added
      in a later phase.

    See module docstring for the long-term provider matrix.
    """
    return (
        prov.is_active
        and prov.protocol == ProviderProtocol.OPENAI
        and bool(prov.api_key)
        and bool(prov.base_url)
        and not _is_netmind(prov.base_url)
        and not _is_openrouter(prov.base_url)
    )


def _normalize_model(base_url: str) -> str:
    """OpenAI / Yunwu / self-hosted whisper.cpp accept the unprefixed
    ``whisper-1``. OpenRouter would need ``openai/whisper-1`` but is
    currently skipped at provider-resolution time (see
    :func:`_is_compatible_provider`); this helper still detects it so
    that when the OpenRouter dispatcher branch lands, the correct model
    id is already wired."""
    return "openai/whisper-1" if _is_openrouter(base_url) else "whisper-1"


async def _resolve_credential(user_id: Optional[str]) -> Optional[WhisperCredential]:
    """Walk the credential fallback chain. Returns ``None`` when no usable
    OpenAI-compatible provider is reachable for this user.

    Priority order (high to low):
      1. user's official OpenAI provider (base_url contains api.openai.com)
         — preferred because it's the canonical Whisper implementation
         (highest quality + most stable contract).
      2. user's other compatible OpenAI-protocol providers — Yunwu /
         OpenRouter / self-hosted whisper.cpp, etc. Same wire contract.
      3. system_default OpenAI-protocol provider (cloud free tier).
      4. ``settings.openai_api_key`` — local .env classic fallback.

    NetMind providers are skipped entirely — their Whisper goes through
    a different ``/v1/generation`` + ``audio_url`` shape that we don't
    have an adapter for. See :func:`_is_netmind`.

    Note: a separate ``WHISPER_API_KEY`` env override was considered and
    dropped. Users who want self-hosted whisper.cpp should add it as a
    regular OpenAI-protocol provider in the user_provider UI (with
    ``base_url=http://...your-whisper.../v1``); the chain above will
    pick it up at Tier 2 without any new config surface.

    All exceptions are swallowed — callers (and ultimately the upload
    route) must never see this raise.
    """
    # Tier 1 + 2: user's own configured OpenAI-protocol provider
    # Two-pass scan: prefer official OpenAI first, then any compatible.
    if user_id:
        try:
            # Lazy import — utils → agent_framework would otherwise be
            # an import-time circular ref.
            from xyz_agent_context.utils.db_factory import get_db_client
            from xyz_agent_context.agent_framework.user_provider_service import (
                UserProviderService,
            )

            db = await get_db_client()
            user_cfg = await UserProviderService(db).get_user_config(user_id)
            if user_cfg and user_cfg.providers:
                # Pass A: official OpenAI wins outright when present
                for prov in user_cfg.providers.values():
                    if _is_compatible_provider(prov) and _is_official_openai(prov.base_url):
                        return WhisperCredential(
                            api_key=prov.api_key,
                            base_url=prov.base_url,
                            model=_normalize_model(prov.base_url),
                            source=f"user_provider:{prov.source.value}:openai",
                        )
                # Pass B: any other compatible provider (Yunwu / OpenRouter / self-hosted)
                for prov in user_cfg.providers.values():
                    if _is_compatible_provider(prov):
                        return WhisperCredential(
                            api_key=prov.api_key,
                            base_url=prov.base_url,
                            model=_normalize_model(prov.base_url),
                            source=f"user_provider:{prov.source.value}",
                        )
        except Exception as e:
            logger.debug(f"user_provider lookup for transcription failed: {e}")

    # Tier 3: system default (cloud free-tier OpenAI-compatible endpoint)
    try:
        from xyz_agent_context.agent_framework.system_provider_service import (
            SystemProviderService,
        )

        sys_svc = SystemProviderService.instance()
        if sys_svc.is_enabled():
            sys_cfg = sys_svc.get_config()
            for prov in (sys_cfg.providers or {}).values():
                if _is_compatible_provider(prov):
                    return WhisperCredential(
                        api_key=prov.api_key,
                        base_url=prov.base_url,
                        model=_normalize_model(prov.base_url),
                        source="system_default",
                    )
    except Exception as e:
        logger.debug(f"system_provider lookup for transcription failed: {e}")

    # Tier 4: classic local .env fallback
    if settings.openai_api_key:
        return WhisperCredential(
            api_key=settings.openai_api_key,
            base_url="https://api.openai.com/v1",
            model=_normalize_model("https://api.openai.com/v1"),
            source="settings.openai",
        )

    return None


_MIME_BY_EXT = {
    ".ogg": "audio/ogg", ".opus": "audio/opus", ".oga": "audio/ogg",
    ".mp3": "audio/mpeg", ".mpeg": "audio/mpeg", ".mpga": "audio/mpeg",
    ".m4a": "audio/mp4", ".mp4": "audio/mp4",
    ".wav": "audio/wav", ".webm": "audio/webm",
    ".flac": "audio/flac", ".amr": "audio/amr",
}


def _guess_mime(path: Path) -> str:
    return _MIME_BY_EXT.get(path.suffix.lower(), "audio/ogg")


async def is_transcription_available(user_id: Optional[str]) -> bool:
    """Cheap pre-check: does this user have ANY usable OpenAI-protocol
    provider?

    Used by the upload route to populate ``transcription_available`` so
    the frontend can decide whether to show a "voice unavailable" toast
    when ``transcript`` comes back null. Never raises.
    """
    cred = await _resolve_credential(user_id)
    return cred is not None


async def transcribe_audio(
    file_path: str,
    user_id: Optional[str] = None,
    language: Optional[str] = None,
) -> Optional[str]:
    """Transcribe an audio file. Returns text on success, ``None`` on any
    failure. **Never raises** — see module docstring.
    """
    cred = await _resolve_credential(user_id)
    if cred is None:
        logger.debug("audio transcription skipped: no OpenAI-protocol provider")
        return None

    path = Path(file_path)
    if not path.is_file():
        logger.warning(f"audio transcription: file missing {file_path}")
        return None
    if path.suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS:
        logger.debug(f"audio transcription: unsupported ext {path.suffix}")
        return None

    size = path.stat().st_size
    if size == 0:
        return None
    if size > WHISPER_MAX_FILE_BYTES:
        logger.warning(
            f"audio transcription: file too large for Whisper "
            f"({size}B > {WHISPER_MAX_FILE_BYTES}B): {path.name}"
        )
        return None

    try:
        return await asyncio.wait_for(
            _call_whisper(path, language, cred),
            timeout=_OVERALL_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        logger.error(
            f"audio transcription: overall timeout {_OVERALL_TIMEOUT_S}s "
            f"for {path.name} via {cred.source}"
        )
        return None
    except Exception as e:
        logger.error(f"audio transcription failed via {cred.source}: {e}")
        return None


async def _call_whisper(
    path: Path,
    language: Optional[str],
    cred: WhisperCredential,
) -> Optional[str]:
    """Hit the OpenAI-compatible ``/audio/transcriptions`` endpoint.

    Multipart form. ``response_format=text`` so we get plain text back
    (no JSON parsing). Retries once on 429 / 5xx. Non-retryable 4xx logs
    and bails immediately.

    GOTCHA: re-open the file every attempt. httpx exhausts the file
    handle on send; reusing the same fp on retry would post 0 bytes.
    """
    url = f"{cred.base_url.rstrip('/')}/audio/transcriptions"
    data = {"model": cred.model, "response_format": "text"}
    if language:
        data["language"] = language

    headers = {"Authorization": f"Bearer {cred.api_key}"}

    async with httpx.AsyncClient(timeout=_HTTPX_TIMEOUT) as client:
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                with path.open("rb") as fp:
                    files = {"file": (path.name, fp, _guess_mime(path))}
                    resp = await client.post(
                        url, data=data, files=files, headers=headers,
                    )
            except httpx.HTTPError as e:
                logger.warning(
                    f"whisper attempt {attempt} via {cred.source}: http error {e}"
                )
                if attempt == _MAX_ATTEMPTS:
                    return None
                await asyncio.sleep(0.5)
                continue

            if resp.status_code == 200:
                text = resp.text.strip()
                return text or None

            if resp.status_code in (429, 500, 502, 503, 504):
                logger.warning(
                    f"whisper attempt {attempt} via {cred.source}: "
                    f"retryable {resp.status_code} {resp.text[:200]}"
                )
                if attempt == _MAX_ATTEMPTS:
                    return None
                await asyncio.sleep(0.5 * attempt)
                continue

            logger.error(
                f"whisper {resp.status_code} via {cred.source}: {resp.text[:200]}"
            )
            return None
    return None
