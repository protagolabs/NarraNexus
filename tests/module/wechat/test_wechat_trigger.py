"""Unit tests for WeChatTrigger's permanent-auth-failure classification.

Regression guard for the zombie-reconnect class of incident (CLAUDE.md
"Incident-derived engineering lessons" #1): a dead iLink session must DISABLE
the credential, while a transient blip must keep reconnecting.
"""
import httpx

from xyz_agent_context.module.wechat_module.wechat_sdk_client import WeChatSDKError
from xyz_agent_context.module.wechat_module.wechat_trigger import WeChatTrigger


def test_dead_session_is_permanent_auth_failure():
    trig = WeChatTrigger()
    # getupdates ret!=0 == session expired / bad token -> stop reconnecting and
    # let the base class disable the credential (else: reconnect storm forever).
    assert trig.is_permanent_auth_failure(WeChatSDKError(1001, "updates")) is True


def test_send_failure_is_not_permanent():
    trig = WeChatTrigger()
    # A send-side ret!=0 is per-message (stale context_token), not a dead login,
    # and never reaches the connect loop anyway — must not disable the account.
    assert trig.is_permanent_auth_failure(WeChatSDKError(500, "send")) is False


def test_transient_network_error_is_not_permanent():
    trig = WeChatTrigger()
    # Network blips must keep retrying under the default backoff, never disable.
    assert trig.is_permanent_auth_failure(httpx.ConnectError("boom")) is False


import asyncio  # noqa: E402

import pytest  # noqa: E402

from xyz_agent_context.module.wechat_module._wechat_credential_manager import (  # noqa: E402
    WeChatCredential,
)
import xyz_agent_context.module.wechat_module.wechat_trigger as _wt  # noqa: E402


def _cred() -> WeChatCredential:
    return WeChatCredential(agent_id="agent_x", bot_token="tok", base_url="http://x")


async def _drive_until(trig, reconnected, *, timeout=2.0):
    """Run connect() as a task, wait for the reconnect signal, then stop it."""
    async def _drive():
        async for _ in trig.connect(_cred()):
            pass
    t = asyncio.create_task(_drive())
    try:
        await asyncio.wait_for(reconnected.wait(), timeout=timeout)
    finally:
        trig.running = False
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_watchdog_reconnects_on_hung_getupdates(monkeypatch):
    """hop1 hang: a getupdates that never returns within the hard timeout is
    force-terminated and the client is reconnected in place — no exception
    escapes, and a fresh client is created (single loop, no second task)."""
    created: list[int] = []
    reconnected = asyncio.Event()

    class _FakeClient:
        def __init__(self, *_a):
            created.append(1)
            if len(created) >= 2:
                reconnected.set()  # a reconnect (2nd client) happened

        async def get_updates(self, cursor):
            await asyncio.sleep(30)  # hang far past the hard timeout

        async def aclose(self):
            pass

    monkeypatch.setattr(_wt, "WeChatSDKClient", _FakeClient)
    trig = WeChatTrigger()
    trig.running = True
    trig.GETUPDATES_HARD_TIMEOUT_SECONDS = 0.05

    await _drive_until(trig, reconnected)
    assert len(created) >= 2  # reconnected at least once


@pytest.mark.asyncio
async def test_watchdog_reconnects_on_no_inbound(monkeypatch):
    """silent-empty: repeated ret=0/empty polls past the no-inbound window
    trigger a proactive reconnect (the case where a dead session returns no
    error field at all)."""
    created: list[int] = []
    reconnected = asyncio.Event()

    class _FakeClient:
        def __init__(self, *_a):
            created.append(1)
            if len(created) >= 2:
                reconnected.set()

        async def get_updates(self, cursor):
            await asyncio.sleep(0.01)  # let idle time accrue deterministically
            return {"ret": 0, "get_updates_buf": "c", "msgs": []}

        async def aclose(self):
            pass

    monkeypatch.setattr(_wt, "WeChatSDKClient", _FakeClient)
    trig = WeChatTrigger()
    trig.running = True
    trig.WATCHDOG_NO_INBOUND_SECONDS = 0.0  # any elapsed idle → reconnect
    trig.POLL_IDLE_SLEEP_SECONDS = 0.0

    await _drive_until(trig, reconnected)
    assert len(created) >= 2


@pytest.mark.asyncio
async def test_healthy_updates_do_not_reconnect(monkeypatch):
    """A steady stream of real messages must NOT trip the watchdog — the
    client is created exactly once and messages flow through."""
    created: list[int] = []
    got: list[str] = []

    class _FakeClient:
        def __init__(self, *_a):
            created.append(1)

        async def get_updates(self, cursor):
            return {"ret": 0, "get_updates_buf": "c", "msgs": [{"from_user_id": "u", "n": len(got)}]}

        async def aclose(self):
            pass

    monkeypatch.setattr(_wt, "WeChatSDKClient", _FakeClient)
    trig = WeChatTrigger()
    trig.running = True

    async def _drive():
        async for msg in trig.connect(_cred()):
            got.append(msg)
            if len(got) >= 3:
                trig.running = False
                break
    await asyncio.wait_for(_drive(), timeout=2.0)
    assert len(created) == 1  # never reconnected
    assert len(got) >= 3
