"""
@file_name: test_manyfold_outbound.py
@author:
@date: 2026-08-10
@description: Tests for utils/manyfold_outbound.py — the managed-reply
declaration set and the platform channel-send client.

Covers: env-driven provider declaration, channel-send URL derivation
(explicit override vs /notify sibling), request payload shape (camelCase,
bearer token), status mapping (sent/queued = success), and the never-raise
surface on network errors / non-2xx / unresolvable env.
"""
from __future__ import annotations

import json

import httpx
import pytest

from xyz_agent_context.utils import manyfold_outbound as mo


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in (
        "NEXUS_MANAGED_REPLY_PROVIDERS",
        "MANYFOLD_SYNC_WEBHOOK_URL",
        "MANYFOLD_SYNC_WEBHOOK_TOKEN",
        "MANYFOLD_RUNTIME_ID",
        "MANYFOLD_CHANNEL_SEND_URL",
    ):
        monkeypatch.delenv(key, raising=False)


def _set_manyfold_env(monkeypatch, *, url="https://api.example.com/api/internal/narranexus-sync/notify"):
    monkeypatch.setenv("MANYFOLD_SYNC_WEBHOOK_URL", url)
    monkeypatch.setenv("MANYFOLD_SYNC_WEBHOOK_TOKEN", "tok-123")
    monkeypatch.setenv("MANYFOLD_RUNTIME_ID", "rt-abc")


# ---------------------------------------------------------------------------
# Declaration set
# ---------------------------------------------------------------------------

class TestDeclaredProviders:
    def test_default_empty(self):
        assert mo.declared_managed_reply_providers() == frozenset()
        assert mo.managed_reply_declared("wechat") is False

    def test_comma_list_with_whitespace_and_case(self, monkeypatch):
        monkeypatch.setenv(
            "NEXUS_MANAGED_REPLY_PROVIDERS", " Wechat, telegram ,,NARRAMESSENGER "
        )
        declared = mo.declared_managed_reply_providers()
        assert declared == frozenset({"wechat", "telegram", "narramessenger"})
        assert mo.managed_reply_declared("wechat") is True
        assert mo.managed_reply_declared("lark") is False


# ---------------------------------------------------------------------------
# Env / URL resolution
# ---------------------------------------------------------------------------

class TestSendEnv:
    def test_absent_env_resolves_none(self):
        assert mo.channel_send_env() is None

    def test_derives_sibling_of_notify(self, monkeypatch):
        _set_manyfold_env(monkeypatch)
        env = mo.channel_send_env()
        assert env is not None
        assert env.url == (
            "https://api.example.com/api/internal/narranexus-sync/channel-send"
        )
        assert env.token == "tok-123"
        assert env.runtime_id == "rt-abc"

    def test_trailing_slash_tolerated(self, monkeypatch):
        _set_manyfold_env(
            monkeypatch,
            url="https://api.example.com/api/internal/narranexus-sync/notify/",
        )
        env = mo.channel_send_env()
        assert env is not None
        assert env.url.endswith("/channel-send")

    def test_explicit_override_wins(self, monkeypatch):
        _set_manyfold_env(monkeypatch)
        monkeypatch.setenv(
            "MANYFOLD_CHANNEL_SEND_URL", "https://other.example.com/send"
        )
        env = mo.channel_send_env()
        assert env is not None
        assert env.url == "https://other.example.com/send"

    def test_webhook_url_not_ending_in_notify_refused(self, monkeypatch):
        _set_manyfold_env(monkeypatch, url="https://api.example.com/api/other")
        assert mo.channel_send_env() is None

    def test_missing_token_refused(self, monkeypatch):
        monkeypatch.setenv(
            "MANYFOLD_SYNC_WEBHOOK_URL",
            "https://api.example.com/api/internal/narranexus-sync/notify",
        )
        monkeypatch.setenv("MANYFOLD_RUNTIME_ID", "rt-abc")
        assert mo.channel_send_env() is None


class TestManagedSendActive:
    def test_requires_both_declaration_and_env(self, monkeypatch):
        assert mo.managed_channel_send_active("wechat") is False
        monkeypatch.setenv("NEXUS_MANAGED_REPLY_PROVIDERS", "wechat")
        assert mo.managed_channel_send_active("wechat") is False  # no env yet
        _set_manyfold_env(monkeypatch)
        assert mo.managed_channel_send_active("wechat") is True
        assert mo.managed_channel_send_active("telegram") is False  # undeclared


# ---------------------------------------------------------------------------
# channel_send client
# ---------------------------------------------------------------------------

class _Recorder:
    def __init__(self, handler):
        self.requests: list[httpx.Request] = []
        self._handler = handler

    def transport(self) -> httpx.MockTransport:
        def _handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return self._handler(request)

        return httpx.MockTransport(_handle)


def _ok_response(status: str = "sent") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "deliveryId": "dlv_1",
            "status": status,
            "providerMessageId": "pm_1",
            "deduplicated": False,
        },
    )


class TestChannelSend:
    @pytest.mark.asyncio
    async def test_posts_camelcase_payload_with_bearer(self, monkeypatch):
        _set_manyfold_env(monkeypatch)
        rec = _Recorder(lambda req: _ok_response())
        monkeypatch.setattr(mo, "_transport_for_tests", rec.transport())

        ok = await mo.channel_send(
            agent_id="agent_x1",
            provider="wechat",
            room_id="wx_user_9",
            text="hello",
            source_message_id="m-77",
        )
        assert ok is True
        assert len(rec.requests) == 1
        req = rec.requests[0]
        assert str(req.url).endswith("/channel-send")
        assert req.headers["authorization"] == "Bearer tok-123"
        body = json.loads(req.content)
        assert body["runtimeId"] == "rt-abc"
        assert body["agentId"] == "agent_x1"
        assert body["provider"] == "wechat"
        assert body["roomId"] == "wx_user_9"
        assert body["text"] == "hello"
        assert body["sourceMessageId"] == "m-77"
        assert body["idempotencyKey"]  # generated, non-empty
        # No snake_case leakage / no recipient fields
        assert "agent_id" not in body and "toUserId" not in body

    @pytest.mark.asyncio
    async def test_any_2xx_with_sent_status_is_success(self, monkeypatch):
        # The platform pins @HttpCode(200) today, but a future 202-accepted
        # must not be misread as failure (managed mode never falls back to
        # direct send, so a false negative reads as a lost reply).
        _set_manyfold_env(monkeypatch)
        rec = _Recorder(
            lambda req: httpx.Response(
                202, json={"deliveryId": "d", "status": "queued",
                           "providerMessageId": None, "deduplicated": False}
            )
        )
        monkeypatch.setattr(mo, "_transport_for_tests", rec.transport())
        assert await mo.channel_send(
            agent_id="a", provider="wechat", room_id="r", text="t"
        ) is True

    @pytest.mark.asyncio
    async def test_queued_counts_as_success(self, monkeypatch):
        _set_manyfold_env(monkeypatch)
        rec = _Recorder(lambda req: _ok_response("queued"))
        monkeypatch.setattr(mo, "_transport_for_tests", rec.transport())
        assert await mo.channel_send(
            agent_id="a", provider="wechat", room_id="r", text="t"
        ) is True

    @pytest.mark.asyncio
    async def test_failed_status_is_false(self, monkeypatch):
        _set_manyfold_env(monkeypatch)
        rec = _Recorder(lambda req: _ok_response("failed"))
        monkeypatch.setattr(mo, "_transport_for_tests", rec.transport())
        assert await mo.channel_send(
            agent_id="a", provider="wechat", room_id="r", text="t"
        ) is False

    @pytest.mark.asyncio
    async def test_non_2xx_is_false_not_raise(self, monkeypatch):
        _set_manyfold_env(monkeypatch)
        rec = _Recorder(lambda req: httpx.Response(403, json={"message": "no inbound"}))
        monkeypatch.setattr(mo, "_transport_for_tests", rec.transport())
        assert await mo.channel_send(
            agent_id="a", provider="wechat", room_id="r", text="t"
        ) is False

    @pytest.mark.asyncio
    async def test_network_error_is_false_not_raise(self, monkeypatch):
        _set_manyfold_env(monkeypatch)

        def _boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("nope", request=request)

        rec = _Recorder(_boom)
        monkeypatch.setattr(mo, "_transport_for_tests", rec.transport())
        assert await mo.channel_send(
            agent_id="a", provider="wechat", room_id="r", text="t"
        ) is False

    @pytest.mark.asyncio
    async def test_unresolvable_env_is_false_without_request(self):
        assert await mo.channel_send(
            agent_id="a", provider="wechat", room_id="r", text="t"
        ) is False
