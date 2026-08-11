"""
@file_name: test_channel_store.py
@author:
@date: 2026-08-10
@description: ChannelCredentialStore seam — REAL parity between DirectStore
and HttpStore (PR-A, blueprint P2 #2), the backend endpoint's 200 raw /
200 {"bound": false} contract, in-band error degradation, the identity-header
forward, and the composition root's env gate.

Mirrors tests/module/test_data_access.py's shape: a fake backend implementing
the ACTUAL route contract (200 raw dict / 200 {"bound": false}; non-2xx only
from transport/middleware), and a fake discord manager giving DirectStore the
same known agents, so parity means "same scenario, same return value" for a
real reason, not a coincidence of two independently-written fakes.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
import pytest

from xyz_agent_context.module.data_access import (
    ChannelDirectStore,
    ChannelHttpStore,
    get_channel_credential_store,
)
from xyz_agent_context.module.data_access.factory import current_identity_headers

AGENT = "agent_39b2b72b823b"


def _patch_http(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    class PatchedClient(real_client):
        def __init__(self, **kw):
            kw.pop("transport", None)
            super().__init__(transport=transport, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", PatchedClient)


# ---------------------------------------------------------------------------
# A fake discord credential manager + a fake backend implementing the REAL
# route contract (backend/routes/agents/channel_credentials.py).
# ---------------------------------------------------------------------------

_RAW = {
    "agent_id": AGENT,
    "bot_token": "super-secret-token",
    "bot_user_id": "9001",
    "bot_username": "Scout",
    "owner_user_id": "1234",
    "owner_name": "Alice",
    "enabled": True,
    "created_at": "2026-08-10T00:00:00+00:00",
    "updated_at": "2026-08-10T00:00:00+00:00",
}


class _FakeCred:
    def __init__(self, raw):
        self._raw = raw

    def to_raw_dict(self):
        return dict(self._raw)


class _FakeDiscordManager:
    """Stands in for DiscordCredentialManager — DirectStore constructs one
    per call via `_manager_class`, so this patches the class the seam
    resolves, not the seam itself (a real dispatch exercise, not a stub)."""

    def __init__(self, db):
        self.db = db

    async def get(self, agent_id):
        return _FakeCred(_RAW) if agent_id == AGENT else None


def _direct_store(monkeypatch):
    async def fake_manager_class(channel):
        assert channel == "discord"
        return _FakeDiscordManager

    monkeypatch.setattr(
        "xyz_agent_context.module.data_access.channel_store._manager_class",
        lambda channel: _FakeDiscordManager if channel == "discord" else (_ for _ in ()).throw(
            ValueError(f"unknown channel: {channel!r}")
        ),
    )

    store = ChannelDirectStore()

    async def fake_db():
        return object()

    store._db = fake_db  # type: ignore[method-assign]
    return store


def _http_store(monkeypatch, *, known_agents=frozenset({AGENT}), status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status, text="rejected")
        parts = request.url.path.split("/")
        # /api/agents/{agent_id}/channels/{channel}/credential
        agent_id = parts[3]
        channel = parts[5]
        assert channel == "discord"
        if agent_id not in known_agents:
            return httpx.Response(200, json={"bound": False})
        return httpx.Response(200, json=_RAW)

    _patch_http(monkeypatch, handler)
    return ChannelHttpStore("http://backend:8000")


# ---------------------------------------------------------------------------
# parity: same scenario, same raw dict
# ---------------------------------------------------------------------------


def test_parity_bound_agent_returns_the_same_raw_dict(monkeypatch):
    direct = _direct_store(monkeypatch)
    http = _http_store(monkeypatch)

    d = asyncio.run(direct.get_credential("discord", AGENT))
    h = asyncio.run(http.get_credential("discord", AGENT))
    assert d == h == _RAW
    assert d["bot_token"] == "super-secret-token"  # the RAW secret, not sanitised


def test_parity_unbound_agent_is_none_on_both(monkeypatch):
    direct = _direct_store(monkeypatch)
    http = _http_store(monkeypatch, known_agents=frozenset())

    # An agent that is bound on NEITHER side (the fake manager returns a cred
    # only for AGENT; the fake backend knows no agents) — parity = both None.
    unbound = "agent_never_bound00"
    assert asyncio.run(direct.get_credential("discord", unbound)) is None
    assert asyncio.run(http.get_credential("discord", unbound)) is None


def test_direct_unknown_channel_raises_a_clear_error(monkeypatch):
    direct = _direct_store(monkeypatch)
    with pytest.raises(ValueError, match="unknown channel"):
        asyncio.run(direct.get_credential("carrier_pigeon", AGENT))


# ---------------------------------------------------------------------------
# HttpStore: in-band degradation, never raises
# ---------------------------------------------------------------------------


def test_http_401_degrades_to_none_in_band(monkeypatch):
    http = _http_store(monkeypatch, status=401)
    out = asyncio.run(http.get_credential("discord", AGENT))
    assert out is None


def test_http_5xx_degrades_to_none_in_band(monkeypatch):
    http = _http_store(monkeypatch, status=503)
    out = asyncio.run(http.get_credential("discord", AGENT))
    assert out is None


def test_http_unreachable_backend_degrades_to_none_in_band(monkeypatch):
    def boom(request):
        raise httpx.ConnectError("no route to backend")

    _patch_http(monkeypatch, boom)
    store = ChannelHttpStore("http://backend:8000")
    out = asyncio.run(store.get_credential("discord", AGENT))
    assert out is None


def test_http_non_json_response_degrades_to_none_in_band(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    _patch_http(monkeypatch, handler)
    store = ChannelHttpStore("http://backend:8000")
    out = asyncio.run(store.get_credential("discord", AGENT))
    assert out is None


# ---------------------------------------------------------------------------
# get_agent_name parity
# ---------------------------------------------------------------------------


def test_get_agent_name_parity(monkeypatch):
    async def fake_get_one(table, filters):
        assert table == "agents"
        return {"agent_name": "Scout"} if filters["agent_id"] == AGENT else None

    class _FakeDb:
        get_one = staticmethod(fake_get_one)

    direct = ChannelDirectStore()

    async def fake_db():
        return _FakeDb()

    direct._db = fake_db  # type: ignore[method-assign]
    d = asyncio.run(direct.get_agent_name(AGENT))
    assert d == "Scout"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/channels/name")
        return httpx.Response(200, json={"agent_name": "Scout"})

    _patch_http(monkeypatch, handler)
    http = ChannelHttpStore("http://backend:8000")
    h = asyncio.run(http.get_agent_name(AGENT))
    assert h == d == "Scout"


def test_get_agent_name_falls_back_to_agent_id_when_missing(monkeypatch):
    async def fake_get_one(table, filters):
        return None

    class _FakeDb:
        get_one = staticmethod(fake_get_one)

    direct = ChannelDirectStore()

    async def fake_db():
        return _FakeDb()

    direct._db = fake_db  # type: ignore[method-assign]
    assert asyncio.run(direct.get_agent_name(AGENT)) == AGENT

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    _patch_http(monkeypatch, handler)
    http = ChannelHttpStore("http://backend:8000")
    assert asyncio.run(http.get_agent_name(AGENT)) == AGENT


# ---------------------------------------------------------------------------
# identity-header forwarding
# ---------------------------------------------------------------------------


def test_http_store_forwards_identity_headers(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=_RAW)

    _patch_http(monkeypatch, handler)
    store = ChannelHttpStore(
        "http://backend:8000",
        identity_headers={"Authorization": "Bearer nx-agent:a~chat"},
    )
    asyncio.run(store.get_credential("discord", AGENT))
    assert seen["auth"] == "Bearer nx-agent:a~chat"


# ---------------------------------------------------------------------------
# composition root
# ---------------------------------------------------------------------------


def test_factory_default_is_direct(monkeypatch):
    monkeypatch.delenv("NARRANEXUS_BACKEND_URL", raising=False)
    assert isinstance(get_channel_credential_store(), ChannelDirectStore)


def test_factory_cloud_is_http(monkeypatch):
    monkeypatch.setenv("NARRANEXUS_BACKEND_URL", "http://backend:8000")
    store = get_channel_credential_store(identity_headers={"authorization": "Bearer nx-agent:a"})
    assert isinstance(store, ChannelHttpStore)
    assert store._base == "http://backend:8000"
    assert store._headers == {"authorization": "Bearer nx-agent:a"}


def test_factory_cloud_uses_ambient_identity_headers_by_default(monkeypatch):
    monkeypatch.setenv("NARRANEXUS_BACKEND_URL", "http://backend:8000")
    monkeypatch.setattr(
        "xyz_agent_context.module.data_access.factory.current_identity_headers",
        lambda: {"authorization": "Bearer nx-agent:ambient"},
    )
    store = get_channel_credential_store()
    assert store._headers == {"authorization": "Bearer nx-agent:ambient"}


# ---------------------------------------------------------------------------
# write leg (blueprint P2): bind / unbind / test_connection
# ---------------------------------------------------------------------------


def _http():
    return ChannelHttpStore("http://backend:8000")


def test_http_unbind_posts_the_route_and_returns_its_json(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        import json
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"success": True, "data": {"unbound": True}})

    _patch_http(monkeypatch, handler)
    out = asyncio.run(_http().unbind("discord", AGENT))
    assert seen["path"] == "/api/discord/unbind"
    assert seen["body"] == {"agent_id": AGENT}
    assert out == {"success": True, "data": {"unbound": True}}


def test_http_bind_posts_agent_id_plus_fields(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _j
        seen["path"] = request.url.path
        seen["body"] = _j.loads(request.content)
        return httpx.Response(200, json={"success": True, "data": {"bot_username": "b"}})

    _patch_http(monkeypatch, handler)
    out = asyncio.run(_http().bind("discord", AGENT, {"bot_token": "tok", "owner_user_id": "o"}))
    assert seen["path"] == "/api/discord/bind"
    assert seen["body"] == {"agent_id": AGENT, "bot_token": "tok", "owner_user_id": "o"}
    assert out["success"] is True


def test_http_write_unreachable_degrades_to_success_false(monkeypatch):
    def boom(request):
        raise httpx.ConnectError("no route")

    _patch_http(monkeypatch, boom)
    out = asyncio.run(_http().unbind("discord", AGENT))
    assert out["success"] is False and "unreachable" in out["error"]


def test_http_write_5xx_degrades_to_success_false(monkeypatch):
    def reject(request):
        return httpx.Response(500, text="nope")

    _patch_http(monkeypatch, reject)
    out = asyncio.run(_http().unbind("discord", AGENT))
    assert out["success"] is False and "500" in out["error"]


def _direct_with_fake_manager(monkeypatch, *, unbind_result):
    class _FakeMgr:
        def __init__(self, db):
            pass

        async def unbind(self, agent_id):
            return unbind_result

    monkeypatch.setattr(
        "xyz_agent_context.module.data_access.channel_store._manager_class",
        lambda channel: _FakeMgr,
    )
    store = ChannelDirectStore()

    async def fake_db():
        return object()

    store._db = fake_db  # type: ignore[method-assign]
    return store


def test_direct_unbind_wraps_like_the_route(monkeypatch):
    ok = _direct_with_fake_manager(monkeypatch, unbind_result=True)
    assert asyncio.run(ok.unbind("discord", AGENT)) == {"success": True, "data": {"unbound": True}}
    missing = _direct_with_fake_manager(monkeypatch, unbind_result=False)
    r = asyncio.run(missing.unbind("discord", AGENT))
    assert r["success"] is False and "bound" in r["error"]


def test_direct_bind_delegates_to_do_bind(monkeypatch):
    captured = {}

    async def fake_do_bind(mgr, agent_id, **fields):
        captured["agent_id"] = agent_id
        captured["fields"] = fields
        return {"success": True, "data": {"ok": 1}}

    import xyz_agent_context.module.discord_module._discord_service as ds
    monkeypatch.setattr(ds, "do_bind", fake_do_bind)
    monkeypatch.setattr(
        "xyz_agent_context.module.data_access.channel_store._manager_class",
        lambda channel: (lambda db: object()),
    )
    store = ChannelDirectStore()

    async def fake_db():
        return object()

    store._db = fake_db  # type: ignore[method-assign]
    out = asyncio.run(store.bind("discord", AGENT, {"bot_token": "t", "owner_user_id": "o"}))
    assert out == {"success": True, "data": {"ok": 1}}
    assert captured == {"agent_id": AGENT, "fields": {"bot_token": "t", "owner_user_id": "o"}}
