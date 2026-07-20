"""
@file_name: _narra_guide.py
@date: 2026-07-20
@description: Live-fetch layer for the narra-cli usage guide (``narra_guide`` MCP
tool).

narra-cli has no bundled skill packs (unlike lark) — its entire agent-facing
documentation is ONE markdown served by the Narra backend at
``{backend_base_url}/api/agent-guide/narra-runtime.md``. Rather than vendor a
copy that goes stale as narra-cli updates, we fetch it live and let the agent
read the latest. Four guardrails (see the design doc):

  1. **URL derived from the credential's backend_base_url** — never hardcode
     test/prod, so the doc always matches the transport the agent is bound to.
  2. **In-process cache + TTL** — it is an inline dependency during agent turns;
     do not re-fetch every call.
  3. **Fallback** — on fetch failure serve the last good copy, else a small
     bundled snapshot; the agent also always has ``narra-cli <domain> --help``.
  4. (version-tracking of the local binary lives in run.sh / Docker, not here.)

Independent per binding rule #3.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import aiohttp
from loguru import logger

_GUIDE_PATH = "/api/agent-guide/narra-runtime.md"
_CACHE_TTL_SECONDS = 600.0
_FETCH_TIMEOUT = 10.0

# backend_base_url -> (fetched_at_monotonic, text)
_cache: dict[str, tuple[float, str]] = {}

_SNAPSHOT_PATH = Path(__file__).parent / "resources" / "narra-runtime.md"


def _now() -> float:
    """Monotonic clock (patched in tests)."""
    return time.monotonic()


def _guide_url(backend_base_url: str) -> str:
    return f"{backend_base_url.rstrip('/')}{_GUIDE_PATH}"


async def _http_get(url: str) -> str:
    """GET the guide markdown (patched in tests). Raises on non-2xx / network."""
    timeout = aiohttp.ClientTimeout(total=_FETCH_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            return await resp.text()


def _bundled_snapshot() -> str:
    """The offline fallback: a vendored snapshot, or a minimal built-in."""
    try:
        return _SNAPSHOT_PATH.read_text(encoding="utf-8")
    except OSError:
        return (
            "# narra-cli (offline fallback)\n\n"
            "The live guide could not be fetched. Use `narra-cli <domain> --help` "
            "for command syntax. Domains: room, im, speech, status.\n"
        )


async def fetch_guide(backend_base_url: str) -> str:
    """Return the narra-cli guide markdown for this backend.

    Serves the in-process cache within the TTL; otherwise re-fetches live. On
    failure serves the last good copy (if any), else the bundled snapshot — so
    a down endpoint never leaves the agent without guidance.
    """
    if not backend_base_url:
        return _bundled_snapshot()

    url = _guide_url(backend_base_url)
    now = _now()
    entry: Optional[tuple[float, str]] = _cache.get(backend_base_url)
    if entry and (now - entry[0]) < _CACHE_TTL_SECONDS:
        return entry[1]

    try:
        text = await _http_get(url)
        if not text or not text.strip():
            raise ValueError("empty guide body")
        _cache[backend_base_url] = (now, text)
        return text
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[narra_guide] live fetch failed ({url}): {e}")
        if entry:  # a stale-but-real live copy beats the vendored snapshot
            return entry[1]
        return _bundled_snapshot()
