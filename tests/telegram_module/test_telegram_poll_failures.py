"""
@file_name: test_telegram_poll_failures.py
@date: 2026-09-09
@description: getUpdates failure classification for the Telegram long-poll.

Dev logs showed ``getUpdates failed`` 115 times across three agents with
no way to tell a revoked token (HTTP 401 ``Unauthorized``) from a second
poller on the same bot (HTTP 409 ``Conflict: terminated by other
getUpdates request``): ``TelegramSDKError`` carried only the description
string, non-JSON and transport failures were flattened to
``client_error:<ExceptionName>``, and a 409 was retried (with
``deleteWebhook``) every second forever.

Contract pinned here, with a fake HTTP layer (no network):
  - ``TelegramSDKError`` carries ``status`` + ``description``; ``str()``
    names both;
  - 401 -> permanent: connect raises, ``is_permanent_auth_failure`` says
    so, the base loop disables the credential ONCE with a readable
    ``disabled_reason``;
  - 409 -> one ``deleteWebhook`` retry (a stale webhook), then permanent;
  - 5xx / transport errors -> transient, the base backoff keeps retrying.
"""
from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
import pytest

from narranexus.platform.channel.credential_store import GenericCredentialStore
from narranexus_plugins.telegram_module import telegram_sdk_client as sdk_mod
from narranexus_plugins.telegram_module import telegram_trigger as trigger_mod
from narranexus_plugins.telegram_module._telegram_credential_manager import (
    TelegramCredential,
    TelegramCredentialManager,
)
from narranexus_plugins.telegram_module.telegram_sdk_client import (
    TelegramSDKClient,
    TelegramSDKError,
)
from narranexus_plugins.telegram_module.telegram_trigger import TelegramTrigger


# ── fake HTTP layer for the SDK client ───────────────────────────────────


class _Resp:
    def __init__(self, status: int, payload: dict | None = None, text: str = ""):
        self.status = status
        self._payload = payload
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None

    async def json(self, **_kw) -> dict:
        if self._payload is None:
            raise aiohttp.ContentTypeError(None, (), message="not json")
        return self._payload

    async def text(self) -> str:
        return self._text


class _Session:
    def __init__(self, responses: list):
        self._responses = list(responses)
        self.closed = False
        self.calls: list[tuple[str, dict]] = []

    def post(self, url: str, json: dict | None = None):
        self.calls.append((url, json or {}))
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self) -> None:
        self.closed = True


def _sdk_with(monkeypatch, responses: list) -> tuple[TelegramSDKClient, _Session]:
    session = _Session(responses)
    monkeypatch.setattr(sdk_mod.aiohttp, "ClientSession", lambda *a, **k: session)
    return TelegramSDKClient("1234:tok"), session


UNAUTHORIZED = _Resp(401, {"ok": False, "error_code": 401, "description": "Unauthorized"})
CONFLICT = _Resp(
    409,
    {
        "ok": False,
        "error_code": 409,
        "description": "Conflict: terminated by other getUpdates request; "
        "make sure that only one bot instance is running",
    },
)


@pytest.mark.asyncio
async def test_sdk_error_carries_status_and_description(monkeypatch):
    client, _ = _sdk_with(monkeypatch, [UNAUTHORIZED])
    with pytest.raises(TelegramSDKError) as exc_info:
        await client.get_updates()
    err = exc_info.value
    assert err.status == 401
    assert err.description == "Unauthorized"
    assert err.code == "Unauthorized"  # existing callers branch on .code
    assert "401" in str(err) and "Unauthorized" in str(err)


@pytest.mark.asyncio
async def test_sdk_error_keeps_non_json_body_and_status(monkeypatch):
    client, _ = _sdk_with(monkeypatch, [_Resp(502, None, "<html>Bad Gateway</html>")])
    with pytest.raises(TelegramSDKError) as exc_info:
        await client.get_updates()
    err = exc_info.value
    assert err.status == 502
    assert "Bad Gateway" in str(err)


@pytest.mark.asyncio
async def test_sdk_error_names_the_transport_exception(monkeypatch):
    client, _ = _sdk_with(monkeypatch, [aiohttp.ClientConnectionError("Cannot connect to host api.telegram.org")])
    with pytest.raises(TelegramSDKError) as exc_info:
        await client.get_updates()
    err = exc_info.value
    assert err.status is None
    assert "ClientConnectionError" in str(err)
    assert "api.telegram.org" in str(err)


@pytest.mark.asyncio
async def test_api_call_envelope_carries_error_code(monkeypatch):
    client, _ = _sdk_with(monkeypatch, [CONFLICT])
    out = await client.api_call("getUpdates", {})
    assert out["ok"] is False
    assert out["error_code"] == 409
    assert out["error"].startswith("Conflict")


# ── trigger classification + poll behaviour ─────────────────────────────


class _FakeClient:
    """Stands in for TelegramSDKClient inside the trigger's connect loop."""

    instances: list["_FakeClient"] = []

    def __init__(self, token: str, script: list):
        self.token = token
        self.script = list(script)
        self.delete_webhook_calls = 0
        _FakeClient.instances.append(self)

    async def get_updates(self, **_kw) -> list[dict]:
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def delete_webhook(self) -> bool:
        self.delete_webhook_calls += 1
        return True

    async def close(self) -> None:
        return None


def _script(monkeypatch, script: list) -> None:
    _FakeClient.instances.clear()
    monkeypatch.setattr(trigger_mod, "TelegramSDKClient", lambda token: _FakeClient(token, script))


def _err(status: int, description: str) -> TelegramSDKError:
    return TelegramSDKError.from_envelope(
        {"ok": False, "error": description, "error_code": status}, "getUpdates failed"
    )


def _cred() -> TelegramCredential:
    return TelegramCredential(agent_id="agent_a", bot_token="1234:tok", bot_user_id="1001", bot_username="acme_bot")


async def _drain(trigger: TelegramTrigger, cred: TelegramCredential) -> list[dict]:
    got = []
    async for raw in trigger.connect(cred):
        got.append(raw)
        trigger.running = False
    return got


@pytest.mark.asyncio
async def test_401_is_permanent_and_raises_out_of_connect(monkeypatch):
    _script(monkeypatch, [_err(401, "Unauthorized")])
    trigger = TelegramTrigger()
    trigger.running = True

    with pytest.raises(TelegramSDKError) as exc_info:
        await _drain(trigger, _cred())
    assert trigger.is_permanent_auth_failure(exc_info.value) is True
    assert "Unauthorized" in str(exc_info.value)


@pytest.mark.asyncio
async def test_409_gets_one_delete_webhook_retry_then_is_permanent(monkeypatch):
    conflict = "Conflict: terminated by other getUpdates request; make sure that only one bot instance is running"
    _script(monkeypatch, [_err(409, conflict), _err(409, conflict), [{"update_id": 1}]])
    trigger = TelegramTrigger()
    trigger.running = True
    monkeypatch.setattr(trigger_mod.asyncio, "sleep", _no_sleep)

    with pytest.raises(TelegramSDKError) as exc_info:
        await _drain(trigger, _cred())
    assert exc_info.value.status == 409
    assert trigger.is_permanent_auth_failure(exc_info.value) is True
    assert _FakeClient.instances[0].delete_webhook_calls == 1
    assert "other getUpdates request" in str(exc_info.value)


@pytest.mark.asyncio
async def test_409_from_a_stale_webhook_recovers_after_delete_webhook(monkeypatch):
    _script(monkeypatch, [_err(409, "Conflict: terminated by setWebhook request"), [{"update_id": 5}]])
    trigger = TelegramTrigger()
    trigger.running = True
    monkeypatch.setattr(trigger_mod.asyncio, "sleep", _no_sleep)

    got = await _drain(trigger, _cred())
    assert got == [{"update_id": 5}]
    assert _FakeClient.instances[0].delete_webhook_calls == 1


@pytest.mark.parametrize(
    "exc",
    [
        _err(500, "Internal Server Error"),
        _err(502, "http_502: <html>Bad Gateway</html>"),
        TelegramSDKError("client_error:TimeoutError", "getUpdates failed (client_error:TimeoutError)"),
        asyncio.TimeoutError(),
    ],
)
@pytest.mark.asyncio
async def test_transient_errors_are_not_permanent(monkeypatch, exc):
    _script(monkeypatch, [exc])
    trigger = TelegramTrigger()
    trigger.running = True

    with pytest.raises(type(exc)):
        await _drain(trigger, _cred())
    assert trigger.is_permanent_auth_failure(exc) is False


async def _no_sleep(*_a, **_k) -> None:
    return None


# ── the base loop disables the credential once, with a readable reason ──


@pytest.mark.asyncio
async def test_subscribe_loop_disables_credential_with_reason_on_401(db_client, monkeypatch):
    store = GenericCredentialStore(db_client)
    await store.upsert("telegram", "agent_a", {"bot_token": "1234:tok", "bot_user_id": "1001", "bot_username": "acme_bot"}, enabled=True)
    _script(monkeypatch, [_err(401, "Unauthorized")])
    trigger = TelegramTrigger()
    trigger._db = db_client
    trigger.running = True

    await trigger._subscribe_loop(_cred())  # returns instead of backing off forever

    mgr = TelegramCredentialManager(db_client)
    cred = await mgr.get("agent_a")
    assert cred.enabled is False
    assert "401" in cred.disabled_reason and "Unauthorized" in cred.disabled_reason
    assert "disabled_reason" in cred.to_public_dict()
    assert await mgr.list_active() == []
    assert len(_FakeClient.instances) == 1  # no reconnect attempt after the permanent failure


@pytest.mark.asyncio
async def test_re_enabling_clears_the_disabled_reason(db_client):
    store = GenericCredentialStore(db_client)
    await store.upsert("telegram", "agent_a", {"bot_token": "1234:tok", "bot_user_id": "1001"}, enabled=True)
    mgr = TelegramCredentialManager(db_client)

    assert await mgr.set_enabled("agent_a", False, reason="TelegramSDKError: getUpdates failed (HTTP 409)") is True
    assert (await mgr.get("agent_a")).disabled_reason.startswith("TelegramSDKError")

    # The generic set-active route re-enables through the store directly.
    assert await store.set_enabled("telegram", "agent_a", True) is True
    cred = await mgr.get("agent_a")
    assert cred.enabled is True
    assert cred.disabled_reason == ""
    assert await store.set_enabled("telegram", "nobody", False, reason="x") is False
