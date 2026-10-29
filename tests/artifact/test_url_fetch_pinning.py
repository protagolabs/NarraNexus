"""
@file_name: test_url_fetch_pinning.py
@author: Bin Liang
@date: 2026-10-29
@description: The URL-artifact fetchers dial through the pinned transport in cloud.

embed_probe and page_text pre-check every redirect hop, but the pre-check
resolves the name separately from the dial. With a rebinding resolver (public
for the pre-check, metadata for the dial) the default client they build must
refuse at connect time — no socket is ever opened.
"""
from __future__ import annotations

import httpcore
import pytest

from narranexus.platform.artifact._artifact_impl.embed_probe import probe_url
from narranexus.platform.artifact._artifact_impl.page_text import fetch_page_text


def _rebinding_resolver():
    answers = iter([["93.184.216.34"]])

    async def resolve(host, port):
        return next(answers, ["169.254.169.254"])

    return resolve


@pytest.fixture
def cloud(monkeypatch):
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud")


@pytest.fixture
def dials(monkeypatch):
    seen = []

    async def spy(self, host, port, timeout=None, local_address=None, socket_options=None):
        seen.append((host, port))
        raise httpcore.ConnectError("test: sockets disabled")

    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", spy)
    return seen


@pytest.mark.asyncio
async def test_embed_probe_refuses_rebinding_at_connect(cloud, dials):
    verdict = await probe_url("http://rebind.example/", our_scheme="https", resolver=_rebinding_resolver())
    assert verdict.probe_status == "failed"
    assert dials == []


@pytest.mark.asyncio
async def test_page_text_refuses_rebinding_at_connect(cloud, dials):
    assert await fetch_page_text("http://rebind.example/", resolver=_rebinding_resolver()) is None
    assert dials == []



@pytest.mark.asyncio
@pytest.mark.parametrize("fetch", ["probe", "text"])
async def test_local_mode_builds_the_default_transport(monkeypatch, fetch):
    # Desktop / run.sh: no pinned transport, so httpx keeps honouring *_PROXY
    # (a user behind a corporate proxy). The per-hop pre-check still applies.
    import httpx

    from narranexus.platform.utils.safe_http import PublicOnlyTransport

    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "local")
    built = []
    real_client = httpx.AsyncClient

    def _capture(*args, **kwargs):
        built.append(kwargs.get("transport"))
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _capture)

    async def _public_then_refuse(host, port):
        raise OSError("test: stop at the pre-check")

    if fetch == "probe":
        await probe_url("http://example.com/", our_scheme="https", resolver=_public_then_refuse)
    else:
        await fetch_page_text("http://example.com/", resolver=_public_then_refuse)
    assert built and not any(isinstance(t, PublicOnlyTransport) for t in built)
