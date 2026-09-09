"""
@file_name: test_channel_generic_routes.py
@author: Bin Liang
@date: 2026-09-04
@description: /api/channels/{channel}/… serves any channel in ingress.channels: schema, bind/credential/test/unbind/set-active for a plugin channel through the generic store, ownership-gated, 404 for unknown channels.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import backend.routes._ownership as own
from backend.routes.channels import generic as generic_mod
from narranexus.contracts.channel import ChannelDescriptor, CredentialField, CredentialSchema
from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES
from narranexus.kernel.plugins.registry import Contribution
from narranexus.platform.channel import credential_codec

PLUGIN = ChannelDescriptor(
    name="acme_chat",
    display_name="Acme Chat",
    transport="webhook",
    credential_schema=CredentialSchema(fields=(CredentialField("bot_token", "secret"), CredentialField("bot_id", required=False)), supports_test=False, external_id_field="bot_id"),
    has_test=False,
)


def _build_app(db_client, monkeypatch):
    """Shared wiring behind both ``client`` fixtures below."""
    async def _db():
        return db_client

    monkeypatch.setattr(own, "get_db_client", _db)
    monkeypatch.setattr(generic_mod, "_db", _db)

    async def _resolve(self, agent_id):
        return "u1" if agent_id.startswith("a") else None

    monkeypatch.setattr(own.AgentRepository, "resolve_owner", _resolve)
    import narranexus.platform.module_system  # noqa: F401

    registry = KERNEL_REGISTRIES.registry_for("ingress.channels")
    dispose = registry.register_contribution(Contribution("acme_chat", lambda: PLUGIN), owner="acme.chat")
    app = FastAPI()

    @app.middleware("http")
    async def _identity(request: Request, call_next):
        request.state.user_id = request.headers.get("X-User-Id")
        return await call_next(request)

    app.include_router(generic_mod.router, prefix="/api/channels")
    return app, dispose


@pytest.fixture
def client(db_client, monkeypatch, tmp_path: Path):
    credential_codec.use_key_dir(tmp_path / "keys")
    app, dispose = _build_app(db_client, monkeypatch)
    yield TestClient(app)
    dispose.dispose()
    credential_codec.use_key_dir(None)


@pytest.fixture
def client_surfacing_server_errors(db_client, monkeypatch, tmp_path: Path):
    """Same wiring as ``client``, but with Starlette's default exception
    re-raise disabled so an unhandled route exception comes back as an
    actual HTTP response (what a real deployment returns) instead of
    propagating into the test process."""
    credential_codec.use_key_dir(tmp_path / "keys")
    app, dispose = _build_app(db_client, monkeypatch)
    yield TestClient(app, raise_server_exceptions=False)
    dispose.dispose()
    credential_codec.use_key_dir(None)


def test_schema_and_unknown_channel(client):
    r = client.get("/api/channels/acme_chat/schema")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["transport"] == "webhook" and data["manager_backed"] is False and [f["name"] for f in data["fields"]] == ["bot_token", "bot_id"]
    assert data["fields"][0]["public"] is False
    assert client.get("/api/channels/nope/schema").status_code == 404
    lark = client.get("/api/channels/lark/schema").json()["data"]
    assert lark["manager_backed"] and lark["display_name"] == "Lark"
    # a manager-backed channel's bind input differs from its stored shape
    assert [f["name"] for f in lark["bind_fields"]] == ["app_id", "app_secret", "brand", "owner_email"]
    assert [f["name"] for f in data["bind_fields"]] == ["bot_token", "bot_id"]  # plugin: stored == bind input


def test_plugin_channel_bind_flow_is_owner_gated(client):
    H = {"X-User-Id": "u1"}
    r = client.post("/api/channels/acme_chat/bind", json={"agent_id": "a1", "fields": {"bot_id": "b1"}}, headers=H)
    assert r.json() == {"success": False, "error": "missing required field(s): bot_token"}
    r = client.post("/api/channels/acme_chat/bind", json={"agent_id": "a1", "fields": {"bot_token": "tok", "bot_id": "b1"}}, headers=H)
    assert r.json()["success"] and r.json()["data"]["external_id"] == "b1" and "bot_token" not in r.json()["data"]
    cred = client.get("/api/channels/acme_chat/credential", params={"agent_id": "a1"}, headers=H).json()["data"]
    assert cred["bot_id"] == "b1" and cred["enabled"] is True
    assert client.post("/api/channels/acme_chat/test", json={"agent_id": "a1"}, headers=H).json()["data"] == {"checked": False, "enabled": True}
    assert client.post("/api/channels/acme_chat/set-active", json={"agent_id": "a1", "active": False}, headers=H).json() == {"success": True, "enabled": False}
    assert client.get("/api/channels/acme_chat/credential", params={"agent_id": "a1"}, headers=H).json()["data"]["enabled"] is False
    # another user cannot read or write it
    other = client.get("/api/channels/acme_chat/credential", params={"agent_id": "a1"}, headers={"X-User-Id": "u2"}).json()
    assert other["success"] is False
    assert client.post("/api/channels/acme_chat/unbind", json={"agent_id": "a1"}, headers=H).json() == {"success": True, "data": {"unbound": True}}
    assert client.post("/api/channels/acme_chat/unbind", json={"agent_id": "a1"}, headers=H).json()["success"] is False


NOT_OWNER_ERROR = "Permission denied: you do not own this agent."


def test_every_verb_is_ownership_gated_against_an_outsider(client):
    """docstring/§name of the retired test claimed the WHOLE surface was
    ownership-gated, but only `credential` was ever actually hit with a
    second user's request. Drive `u2` against all five verbs and assert the
    CONCRETE deny reason (not just `success is False`, which a 500 or a
    validation error would also satisfy) — plus the owner-allowed case for
    contrast, so a fail-open regression (permission check silently dropped)
    cannot pass by both sides going green.

    This route family never raises an HTTP 403/404 for a denied write —
    `check_owned` (unlike its sibling `assert_owned`) always returns 200 with
    a `{"success": False, "error": ...}` envelope so callers get a uniform
    JSON shape to render; that is the deliberate contract here, not merely
    "haven't gotten around to it yet".
    """
    owner = {"X-User-Id": "u1"}
    outsider = {"X-User-Id": "u2"}
    bind_body = {"agent_id": "a1", "fields": {"bot_token": "tok", "bot_id": "b1"}}

    # Outsider: every verb 200s with the concrete "not owner" envelope.
    r = client.post("/api/channels/acme_chat/bind", json=bind_body, headers=outsider)
    assert r.status_code == 200 and r.json() == {"success": False, "error": NOT_OWNER_ERROR}

    r = client.get("/api/channels/acme_chat/credential", params={"agent_id": "a1"}, headers=outsider)
    assert r.status_code == 200 and r.json() == {"success": False, "error": NOT_OWNER_ERROR}

    r = client.post("/api/channels/acme_chat/test", json={"agent_id": "a1"}, headers=outsider)
    assert r.status_code == 200 and r.json() == {"success": False, "error": NOT_OWNER_ERROR}

    r = client.post("/api/channels/acme_chat/unbind", json={"agent_id": "a1"}, headers=outsider)
    assert r.status_code == 200 and r.json() == {"success": False, "error": NOT_OWNER_ERROR}

    r = client.post("/api/channels/acme_chat/set-active", json={"agent_id": "a1", "active": True}, headers=outsider)
    assert r.status_code == 200 and r.json() == {"success": False, "error": NOT_OWNER_ERROR}

    # Owner: every verb actually goes through (the allow-path this file must
    # keep green — a fail-closed regression that denied everyone would still
    # pass the outsider assertions above).
    r = client.post("/api/channels/acme_chat/bind", json=bind_body, headers=owner)
    assert r.status_code == 200 and r.json()["success"] is True
    r = client.get("/api/channels/acme_chat/credential", params={"agent_id": "a1"}, headers=owner)
    assert r.status_code == 200 and r.json()["success"] is True
    r = client.post("/api/channels/acme_chat/test", json={"agent_id": "a1"}, headers=owner)
    assert r.status_code == 200 and r.json()["success"] is True
    r = client.post("/api/channels/acme_chat/set-active", json={"agent_id": "a1", "active": False}, headers=owner)
    assert r.status_code == 200 and r.json() == {"success": True, "enabled": False}
    r = client.post("/api/channels/acme_chat/unbind", json={"agent_id": "a1"}, headers=owner)
    assert r.status_code == 200 and r.json() == {"success": True, "data": {"unbound": True}}


def test_builtin_bind_body_is_checked_against_bind_fields(client, monkeypatch):
    """The retired per-channel routes carried Pydantic models; the generic route
    checks the body against the descriptor's bind_fields BEFORE the service runs —
    unknown names (they would reach do_bind(**fields)), missing required ones and
    a bad select value are envelopes, never a TypeError/500."""
    H = {"X-User-Id": "u1"}
    called = []

    async def _bind(self, channel, agent_id, fields):
        called.append((channel, agent_id, fields))
        return {"success": True, "data": {"bound": True}}

    from narranexus.platform.module_system.data_access import channel_store

    monkeypatch.setattr(channel_store.DirectStore, "bind", _bind)
    r = client.post("/api/channels/lark/bind", json={"agent_id": "a1", "fields": {"app_id": "cli_1", "app_secret": "s", "brand": "feishu", "evil": "x"}}, headers=H)
    assert r.json() == {"success": False, "error": "unknown field(s): evil"}
    r = client.post("/api/channels/lark/bind", json={"agent_id": "a1", "fields": {"app_id": "cli_1", "app_secret": "s"}}, headers=H)
    assert r.json() == {"success": False, "error": "missing required field(s): brand"}
    r = client.post("/api/channels/lark/bind", json={"agent_id": "a1", "fields": {"app_id": "cli_1", "app_secret": "s", "brand": "wechat"}}, headers=H)
    assert r.json() == {"success": False, "error": "brand must be one of: feishu, lark"}
    r = client.post("/api/channels/narramessenger/bind", json={"agent_id": "a1", "fields": {"bind_command": "https://x/setup-guide.md"}}, headers=H)
    assert r.json()["success"] is True
    assert called == [("narramessenger", "a1", {"bind_command": "https://x/setup-guide.md"})]
    # the QR-only channel has no bind call at all
    assert client.post("/api/channels/wechat/bind", json={"agent_id": "a1", "fields": {"bot_token": "t"}}, headers=H).json()["success"] is False


def test_builtin_set_active_and_credential_read_the_generic_store(client, db_client):
    """Lark's binding flips through the same store row as every channel (its
    manager has set_is_active, not set_enabled — the route no longer cares)."""
    from narranexus_plugins.lark_module._lark_credential_manager import LarkCredential, LarkCredentialManager, _encode_secret

    H = {"X-User-Id": "u1"}
    asyncio_run = __import__("asyncio").run
    asyncio_run(LarkCredentialManager(db_client).save_credential(LarkCredential(
        agent_id="a1", app_id="cli_x", app_secret_ref="r", app_secret_encoded=_encode_secret("s"), brand="feishu",
        profile_name="p", auth_status="bot_ready", is_active=False,
    )))
    cred = client.get("/api/channels/lark/credential", params={"agent_id": "a1"}, headers=H).json()["data"]
    assert cred["enabled"] is False and cred["app_id"] == "cli_x" and cred["auth_status"] == "bot_ready"
    assert "app_secret_encoded" not in cred and "app_secret_ref" not in cred
    assert client.post("/api/channels/lark/set-active", json={"agent_id": "a1", "active": True}, headers=H).json() == {"success": True, "enabled": True}
    assert asyncio_run(LarkCredentialManager(db_client).get_credential("a1")).is_active is True
    assert client.post("/api/channels/lark/set-active", json={"agent_id": "a2", "active": True}, headers=H).json()["success"] is False  # owned, unbound


def test_bind_survives_an_unexpected_exception(client_surfacing_server_errors, monkeypatch):
    """B-31 sweep: the generic ``/bind`` route (what telegram/discord/slack
    all bind through) had the same gap as the Lark OAuth routes — an
    unexpected exception past the store's own typed ``CredentialConflict``
    handling propagated out of the route entirely. Starlette's default
    error middleware turns that into a plain-text 500 the frontend cannot
    read an ``error`` field out of. This must come back as the same
    structured ``{"success": False, "error": ...}`` envelope every other
    outcome on this route already uses.
    """
    from narranexus.platform.channel.credential_store import GenericCredentialStore

    async def _boom(self, channel, agent_id, values, *, enabled=None):
        raise RuntimeError("unexpected boom")

    monkeypatch.setattr(GenericCredentialStore, "upsert", _boom)

    H = {"X-User-Id": "u1"}
    r = client_surfacing_server_errors.post(
        "/api/channels/acme_chat/bind",
        json={"agent_id": "a1", "fields": {"bot_token": "tok", "bot_id": "b1"}},
        headers=H,
    )
    assert r.headers.get("content-type", "").startswith("application/json"), (
        f"expected structured JSON, got {r.headers.get('content-type')!r}: {r.text!r}"
    )
    body = r.json()
    assert body["success"] is False
    assert "boom" in body["error"]
