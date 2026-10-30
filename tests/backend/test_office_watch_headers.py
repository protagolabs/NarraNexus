"""
@file_name: test_office_watch_headers.py
@author: Bin Liang
@date: 2026-10-30
@description: Response headers and type allowlist of the public office-watch proxy.

The proxy serves document-controlled content from the app's origin. The viewer's
iframe sandbox only binds that one frame, so every response has to be inert by
itself. A real aiohttp server stands in for `officecli watch`; only the seam that
picks its address is replaced, so the upstream headers go through the real proxy
code path.
"""
from __future__ import annotations

import httpx
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from fastapi import FastAPI

import backend.routes.office_watch.proxy as owp
from backend.routes.office_watch._token import mint
from narranexus.platform.utils.office_watch import is_watch_port

WATCH_PORT = next(p for p in range(1024, 65536) if is_watch_port(p))
HTML = "<html><head></head><body>slide</body></html>"


def _typed(body: bytes, content_type: str, status: int = 200):
    async def handler(_request):
        return web.Response(body=body, status=status, headers={"Content-Type": content_type})

    return handler


async def _events(request):
    resp = web.StreamResponse(headers={"Content-Type": "text/event-stream; charset=utf-8"})
    await resp.prepare(request)
    await resp.write(b"data: one\n\n")
    await resp.write(b"data: two\n\n")
    await resp.write_eof()
    return resp


@pytest.fixture
async def proxy(monkeypatch):
    monkeypatch.setattr(
        "narranexus.platform.settings.settings.transcription_hmac_secret",
        "test-secret",
        raising=False,
    )
    upstream = web.Application()
    upstream.router.add_get("/", _typed(HTML.encode(), "text/html; charset=utf-8"))
    upstream.router.add_get("/shouty", _typed(HTML.encode(), "TEXT/HTML"))
    upstream.router.add_get("/events", _events)
    upstream.router.add_get("/assets/app.js", _typed(b"run()", "text/javascript"))
    upstream.router.add_get("/assets/app.css", _typed(b"a{}", "text/css; charset=utf-8"))
    upstream.router.add_get("/assets/f.woff2", _typed(b"wOF2", "font/woff2"))
    upstream.router.add_get("/assets/p.png", _typed(b"\x89PNG", "image/png"))
    upstream.router.add_get("/assets/x.svg", _typed(b"<svg onload=x()/>", "image/svg+xml"))
    upstream.router.add_get("/doc.xhtml", _typed(b"<x/>", "application/xhtml+xml"))
    upstream.router.add_get("/odd", _typed(b"<x/>", "application/x-text/html-ish"))
    upstream.router.add_get("/api/nope", _typed(b"Not Found", "text/plain; charset=utf-8", 404))
    upstream.router.add_post("/api/selection", _typed(b"<script>x()</script>", "text/html"))
    server = TestServer(upstream)
    await server.start_server()

    async def to_test_server(user_id, port, path, query):
        return f"http://127.0.0.1:{server.port}/{path}", {}

    monkeypatch.setattr(owp, "_resolve_upstream", to_test_server)
    app = FastAPI()
    app.include_router(owp.public_router, prefix="/api/public")
    prefix = f"/api/public/office-watch-proxy/{mint(user_id='u', port=WATCH_PORT)}/{WATCH_PORT}"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield {"client": client, "prefix": prefix}
    await server.close()


def _assert_inert(response) -> None:
    assert response.headers["content-security-policy"] == "sandbox allow-scripts"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    # The page is opaque-origin, so its own EventSource/fetch stay CORS-gated.
    assert response.headers["access-control-allow-origin"] == "*"


async def test_html_document_is_sandboxed_and_rewritten(proxy):
    r = await proxy["client"].get(f"{proxy['prefix']}/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert '<base href="' in r.text
    assert "content-disposition" not in r.headers
    _assert_inert(r)


async def test_html_type_is_matched_case_insensitively_and_still_rewritten(proxy):
    r = await proxy["client"].get(f"{proxy['prefix']}/shouty")
    assert '<base href="' in r.text
    _assert_inert(r)


async def test_sse_stream_passes_frames_through_with_the_headers(proxy):
    r = await proxy["client"].get(f"{proxy['prefix']}/events")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.content == b"data: one\n\ndata: two\n\n"
    assert "content-disposition" not in r.headers
    _assert_inert(r)


@pytest.mark.parametrize(
    "path,expected_type,body",
    [
        ("assets/app.js", "text/javascript", b"run()"),
        ("assets/app.css", "text/css", b"a{}"),
        ("assets/f.woff2", "font/woff2", b"wOF2"),
        ("assets/p.png", "image/png", b"\x89PNG"),
    ],
)
async def test_allowed_asset_keeps_its_type_so_nosniff_lets_it_load(proxy, path, expected_type, body):
    r = await proxy["client"].get(f"{proxy['prefix']}/{path}")
    assert r.headers["content-type"].startswith(expected_type)
    assert r.content == body
    assert "content-disposition" not in r.headers
    _assert_inert(r)


@pytest.mark.parametrize("path", ["assets/x.svg", "doc.xhtml", "odd"])
async def test_a_type_outside_the_allowlist_becomes_a_download(proxy, path):
    r = await proxy["client"].get(f"{proxy['prefix']}/{path}")
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["content-disposition"] == "attachment"
    assert b"<base" not in r.content  # never treated as the html document
    _assert_inert(r)


async def test_officecli_plain_text_error_stays_readable(proxy):
    r = await proxy["client"].get(f"{proxy['prefix']}/api/nope")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("text/plain")
    assert "content-disposition" not in r.headers
    _assert_inert(r)


async def test_post_answer_is_labelled_json_whatever_the_upstream_said(proxy):
    r = await proxy["client"].post(f"{proxy['prefix']}/api/selection", content=b"{}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    _assert_inert(r)
