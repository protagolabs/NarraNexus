"""
@file_name: page_text.py
@author: Bin Liang
@date: 2026-07-22
@description: Best-effort readable-text extraction for a URL tab.

When a URL tab is opened, the agent should be able to SEE what the page says —
not just that a tab exists. This fetches the page server-side and extracts a
bounded plain-text snapshot; `open_url` writes it into the tab's workspace dir
(`content.md`) so the agent can Read it. Visual (rendered-screenshot) vision
is a separate, heavier capability (the RenderService, 方案三) — this is the
cheap text-level answer.

Safety / bounds (this is a server-side fetch of a user/agent-supplied URL):
- Every hop passes the SSRF gate ([[url_safety.py]]); redirects are followed
  manually so an internal redirect target is caught.
- The body read is HARD-CAPPED (`_MAX_FETCH_BYTES`) — we stop reading mid-
  stream, never pulling an unbounded body into memory.
- Only text/html (or text/*) is extracted; other content types return None.
- Output is capped (`_MAX_TEXT_CHARS`). Never raises — a failure returns None,
  and the caller falls back to a "text unavailable" note.
"""

from __future__ import annotations

import re
from html import unescape
from typing import Optional
from urllib.parse import urljoin

import httpx
from loguru import logger

from xyz_agent_context.utils.url_safety import (
    Resolver,
    UnsafeUrlError,
    assert_public_http_url,
)

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
_TIMEOUT_S = 6.0
_MAX_REDIRECTS = 5
_MAX_FETCH_BYTES = 1_000_000  # read at most ~1 MB of body
_MAX_TEXT_CHARS = 16_000      # cap the extracted text handed to the agent

_SCRIPT_STYLE = re.compile(r"<(script|style|noscript|template)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_BLOCK_END = re.compile(r"</(p|div|section|article|li|tr|h[1-6]|header|footer)\s*>|<br\s*/?>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_INLINE_WS = re.compile(r"[ \t\r\f\v]+")
_MULTI_NL = re.compile(r"\n[ \t]*\n[ \t]*(?:\n\s*)+")


def html_to_text(html: str) -> str:
    """Crude, dependency-free HTML → readable text. Not a full parser — good
    enough to give the agent the gist of a page's content."""
    s = _SCRIPT_STYLE.sub(" ", html)
    s = _BLOCK_END.sub("\n", s)  # turn block-ends into line breaks
    s = _TAG.sub("", s)
    s = unescape(s)
    s = _INLINE_WS.sub(" ", s)
    s = _MULTI_NL.sub("\n\n", s)
    return s.strip()


async def fetch_page_text(
    url: str,
    *,
    resolver: Optional[Resolver] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[str]:
    """Fetch `url` and return a bounded plain-text snapshot, or None.

    Best-effort: any failure (network, SSRF on a hop, non-HTML, empty) returns
    None rather than raising, so it never breaks tab creation.
    """
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(follow_redirects=False, timeout=_TIMEOUT_S)
    try:
        current = url
        for _ in range(_MAX_REDIRECTS + 1):
            await assert_public_http_url(current, resolver=resolver)
            async with client.stream("GET", current, headers={"User-Agent": _BROWSER_UA}) as resp:
                if resp.is_redirect and "location" in resp.headers:
                    current = urljoin(current, resp.headers["location"])
                    continue
                ctype = resp.headers.get("content-type", "").lower()
                if "html" not in ctype and "text/" not in ctype:
                    return None  # not a text page — nothing to extract
                total = 0
                chunks: list[bytes] = []
                async for chunk in resp.aiter_bytes():
                    chunks.append(chunk)
                    total += len(chunk)
                    if total >= _MAX_FETCH_BYTES:
                        break
                raw = b"".join(chunks).decode(resp.encoding or "utf-8", errors="replace")
                text = html_to_text(raw)[:_MAX_TEXT_CHARS].strip()
                return text or None
        logger.info("page_text: redirect limit hit for {}", url)
        return None
    except UnsafeUrlError as e:
        logger.warning("page_text: SSRF-blocked a hop for {}: {}", url, e)
        return None
    except Exception as e:  # noqa: BLE001 — best-effort; a failure just means no text
        logger.info("page_text: fetch failed for {}: {}", url, e)
        return None
    finally:
        if owns_client:
            await client.aclose()
