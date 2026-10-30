"""
@file_name: test_http_store_path_segments.py
@author: Bin Liang
@date: 2026-10-30
@description: HttpStore keeps every model-supplied id inside ONE URL path segment.

An agent id comes from the model or a header the agent process sets. Interpolated
raw, `.x/files#` retargets the request to another route with a truncated path, and
a dot segment is collapsed by httpx. Every method is driven through the real httpx
URL builder; only the network hop is replaced.
"""
from __future__ import annotations

import inspect

import httpx
import pytest

from narranexus.platform.module_system.data_access import channel_store
from narranexus.platform.module_system.data_access.store import HttpStore, _seg

AGENT_PARAMS = ("agent_id", "creator_agent_id")


@pytest.fixture
def seen(monkeypatch):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"success": True})

    real = httpx.AsyncClient

    class Recording(real):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Recording)
    return requests


def _filler(param: inspect.Parameter):
    annotation = str(param.annotation)
    if "int" in annotation:
        return 1
    if "list" in annotation:
        return ["x"]
    if "dict" in annotation:
        return {}
    if "bool" in annotation:
        return True
    return "x"


def _agent_methods() -> list[str]:
    return [
        name
        for name, fn in inspect.getmembers(HttpStore, inspect.iscoroutinefunction)
        if not name.startswith("_")
        and any(p in inspect.signature(fn).parameters for p in AGENT_PARAMS)
    ]


async def _drive_every_method(agent_id: str) -> None:
    store = HttpStore("http://backend:8000")
    for name in _agent_methods():
        fn = getattr(store, name)
        kwargs = {
            pname: agent_id if pname in AGENT_PARAMS else _filler(param)
            for pname, param in inspect.signature(fn).parameters.items()
            if pname in AGENT_PARAMS or param.default is inspect.Parameter.empty
        }
        await fn(**kwargs)


def test_the_sweep_reaches_the_methods_it_claims():
    assert len(_agent_methods()) >= 23


@pytest.mark.parametrize(
    "agent_id,encoded",
    [
        (".x/files#", b".x%2Ffiles%23"),
        ("a?b=c", b"a%3Fb%3Dc"),
        ("..", b"%2E%2E"),
        (".", b"%2E"),
        ("../../admin", b"..%2F..%2Fadmin"),
    ],
)
async def test_a_hostile_agent_id_stays_one_segment_on_every_method(seen, agent_id, encoded):
    await _drive_every_method(agent_id)
    assert len(seen) >= 23
    prefix = b"/api/agents/" + encoded + b"/"
    for request in seen:
        assert request.url.raw_path.startswith(prefix), request.url.raw_path


async def test_an_ordinary_agent_id_is_untouched(seen):
    await _drive_every_method("agent_39b2b72b823b")
    assert seen
    for request in seen:
        assert request.url.raw_path.startswith(b"/api/agents/agent_39b2b72b823b/")


def test_channel_store_shares_the_same_encoder():
    assert channel_store._seg("..") == _seg("..") == "%2E%2E"
