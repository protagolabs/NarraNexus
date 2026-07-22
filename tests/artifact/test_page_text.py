"""
@file_name: test_page_text.py
@author: Bin Liang
@date: 2026-07-22
@description: Tests for page-text extraction (the agent-visibility feature).

`html_to_text` is a pure function tested directly; `fetch_page_text` is tested
against httpx MockTransport so bounds, non-HTML skip, redirect-SSRF, and
network failures are deterministic and network-free.
"""
from __future__ import annotations

import httpx
import pytest

from xyz_agent_context.artifact._artifact_impl.page_text import (
    fetch_page_text,
    html_to_text,
)


# ── pure extractor ───────────────────────────────────────────────────────────


def test_html_to_text_strips_scripts_styles_tags_and_unescapes():
    html = (
        "<html><head><style>a{color:red}</style></head>"
        "<body><h1>Hi</h1><p>Hello &amp; welcome</p>"
        "<script>evil()</script><p>Bye</p></body></html>"
    )
    text = html_to_text(html)
    assert "evil()" not in text
    assert "color:red" not in text
    assert "Hello & welcome" in text
    assert "Hi" in text and "Bye" in text


def test_html_to_text_block_breaks():
    assert "\n" in html_to_text("<p>one</p><p>two</p>")


# ── async fetch ──────────────────────────────────────────────────────────────


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


async def _ok_resolver(host, port):
    return ["93.184.216.34"]


@pytest.mark.asyncio
async def test_fetch_extracts_text_from_html():
    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html"},
                              text="<h1>Report</h1><p>Q3 revenue up</p>")
    async with _client(handler) as c:
        text = await fetch_page_text("https://ok.example/", client=c, resolver=_ok_resolver)
    assert text is not None
    assert "Report" in text and "Q3 revenue up" in text


@pytest.mark.asyncio
async def test_fetch_non_html_returns_none():
    def handler(request):
        return httpx.Response(200, headers={"content-type": "application/json"}, text='{"x":1}')
    async with _client(handler) as c:
        assert await fetch_page_text("https://api.example/", client=c, resolver=_ok_resolver) is None


@pytest.mark.asyncio
async def test_fetch_follows_redirect_then_extracts():
    def handler(request):
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "https://d.example/end"})
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<p>final</p>")
    async with _client(handler) as c:
        text = await fetch_page_text("https://r.example/start", client=c, resolver=_ok_resolver)
    assert text == "final"


@pytest.mark.asyncio
async def test_fetch_redirect_to_internal_returns_none_not_raise():
    def handler(request):
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/"})
    async with _client(handler) as c:
        # no resolver override → the metadata IP is literal, SSRF-blocked → None
        assert await fetch_page_text("https://evil.example/", client=c) is None


@pytest.mark.asyncio
async def test_fetch_network_error_returns_none():
    def handler(request):
        raise httpx.ConnectError("down")
    async with _client(handler) as c:
        assert await fetch_page_text("https://down.example/", client=c, resolver=_ok_resolver) is None


@pytest.mark.asyncio
async def test_fetch_body_is_byte_capped():
    # A huge body must not blow up — extraction returns a capped, non-empty str.
    big = "<p>" + ("word " * 500_000) + "</p>"
    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html"}, text=big)
    async with _client(handler) as c:
        text = await fetch_page_text("https://big.example/", client=c, resolver=_ok_resolver)
    assert text is not None
    assert len(text) <= 16_000  # _MAX_TEXT_CHARS
