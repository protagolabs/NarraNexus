"""
@file_name: test_url_fetch_pinning.py
@author: Bin Liang
@date: 2026-10-29
@description: The URL-artifact fetchers dial through the pinned transport.

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
def dials(monkeypatch):
    seen = []

    async def spy(self, host, port, timeout=None, local_address=None, socket_options=None):
        seen.append((host, port))
        raise httpcore.ConnectError("test: sockets disabled")

    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", spy)
    return seen


@pytest.mark.asyncio
async def test_embed_probe_refuses_rebinding_at_connect(dials):
    verdict = await probe_url("http://rebind.example/", our_scheme="https", resolver=_rebinding_resolver())
    assert verdict.probe_status == "failed"
    assert dials == []


@pytest.mark.asyncio
async def test_page_text_refuses_rebinding_at_connect(dials):
    assert await fetch_page_text("http://rebind.example/", resolver=_rebinding_resolver()) is None
    assert dials == []
