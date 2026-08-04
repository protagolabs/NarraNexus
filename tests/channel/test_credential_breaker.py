"""
@file_name: test_credential_breaker.py
@author: Bin Liang
@date: 2026-08-04
@description: Fast-death circuit breaker for subscriber restarts.

Regression (prod, 2026-08-01..03): an agent's LarkSuite App Secret was
cleared, so its subscriber exited immediately on every start (a silent
`return`, not an exception — `is_permanent_auth_failure` never sees it).
`_credential_watcher` reaped the dead task and unconditionally restarted
the same key on the next poll, forever: ~28s death/rebirth cycles flooding
the logs (1498 restarts in a 4h window) with no isolation and no recovery
path other than manually deleting the credential row.

Fix (ChannelTriggerBase, so every channel benefits): the watcher counts
consecutive *fast* deaths per subscriber key (lifetime below
BREAKER_FAST_DEATH_SECONDS). At BREAKER_FAST_DEATH_THRESHOLD the key is
isolated with an escalating re-probe schedule
(BREAKER_BACKOFF_SCHEDULE_SECONDS). A healthy lifetime resets the counters;
a changed credential (user re-bound) clears the breaker immediately; a
removed credential purges all breaker state for the key.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from xyz_agent_context.channel.channel_trigger_base import ChannelTriggerBase
from xyz_agent_context.schema.hook_schema import WorkingSource


@dataclass
class _Credential:
    agent_id: str = "agent_a"
    app_id: str = "bot1"
    app_secret: str = ""


class _BreakerTrigger(ChannelTriggerBase):
    """Subscriber dies instantly (empty-secret shape: return, no raise)."""

    channel_name = "fake"
    brand_display = "Fake"
    working_source = WorkingSource.LARK
    CREDENTIAL_POLL_INTERVAL_SECONDS = 0.05
    IDLE_POLL_INTERVAL_SECONDS = 0.05
    BREAKER_FAST_DEATH_SECONDS = 60.0
    BREAKER_BACKOFF_SCHEDULE_SECONDS = (9999.0,)

    def __init__(self):
        super().__init__(base_workers=1)
        self.loop_starts = 0
        self.subscribe_lifetime = 0.0
        self.creds: list[_Credential] = [_Credential()]

    async def load_active_credentials(self):
        return list(self.creds)

    async def _subscribe_loop(self, credential):
        self.loop_starts += 1
        if self.subscribe_lifetime:
            await asyncio.sleep(self.subscribe_lifetime)

    async def connect(self, credential):  # pragma: no cover - loop overridden
        if False:
            yield {}

    def parse_event(self, raw):  # pragma: no cover - unused
        return None

    async def is_echo(self, message, credential):
        return False

    async def resolve_sender_name(self, sender_id, credential):
        return "x"

    def create_context_builder(self, message, credential, agent_id):
        return None


async def _wait_until(predicate, timeout=4.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()


async def _settled_value(getter, settle_polls=8, interval=0.06):
    """Wait until `getter()` stops changing for `settle_polls` consecutive
    polls, then return the settled value."""
    stable = 0
    last = getter()
    while stable < settle_polls:
        await asyncio.sleep(interval)
        cur = getter()
        if cur == last:
            stable += 1
        else:
            stable = 0
            last = cur
    return last


@pytest.mark.asyncio
async def test_fast_deaths_trip_breaker_and_stop_restarts(db_client):
    trigger = _BreakerTrigger()
    await trigger.start(db_client)
    try:
        assert await _wait_until(
            lambda: trigger.loop_starts >= trigger.BREAKER_FAST_DEATH_THRESHOLD
        )
        settled = await _settled_value(lambda: trigger.loop_starts)
        assert settled == trigger.BREAKER_FAST_DEATH_THRESHOLD
        assert "bot1" in trigger._breaker_blocked_until
    finally:
        await trigger.stop()


@pytest.mark.asyncio
async def test_healthy_lifetime_resets_breaker(db_client):
    trigger = _BreakerTrigger()
    trigger.BREAKER_FAST_DEATH_SECONDS = 0.01
    trigger.subscribe_lifetime = 0.1  # dies, but "healthy" (>= threshold)
    await trigger.start(db_client)
    try:
        # Well past the threshold count: restarts must keep coming.
        assert await _wait_until(
            lambda: trigger.loop_starts
            >= trigger.BREAKER_FAST_DEATH_THRESHOLD + 2
        )
        assert "bot1" not in trigger._breaker_blocked_until
    finally:
        await trigger.stop()


@pytest.mark.asyncio
async def test_backoff_expiry_reprobes(db_client):
    trigger = _BreakerTrigger()
    trigger.BREAKER_BACKOFF_SCHEDULE_SECONDS = (0.15,)
    await trigger.start(db_client)
    try:
        assert await _wait_until(
            lambda: trigger.loop_starts
            > trigger.BREAKER_FAST_DEATH_THRESHOLD
        )
    finally:
        await trigger.stop()


@pytest.mark.asyncio
async def test_second_trip_uses_next_backoff_tier(db_client):
    trigger = _BreakerTrigger()
    trigger.BREAKER_BACKOFF_SCHEDULE_SECONDS = (0.1, 9999.0)
    await trigger.start(db_client)
    try:
        # First trip re-probes after 0.1s; second trip lands on 9999s.
        assert await _wait_until(
            lambda: trigger.loop_starts
            >= 2 * trigger.BREAKER_FAST_DEATH_THRESHOLD
        )
        settled = await _settled_value(lambda: trigger.loop_starts)
        assert settled == 2 * trigger.BREAKER_FAST_DEATH_THRESHOLD
        loop = asyncio.get_event_loop()
        remaining = trigger._breaker_blocked_until["bot1"] - loop.time()
        # Event-loop time and monotonic share the clock family; a wide
        # margin is enough to prove tier 2 (9999s) was applied, not 0.1s.
        assert remaining > 100
    finally:
        await trigger.stop()


@pytest.mark.asyncio
async def test_credential_update_clears_breaker(db_client):
    trigger = _BreakerTrigger()
    await trigger.start(db_client)
    try:
        assert await _wait_until(
            lambda: trigger.loop_starts >= trigger.BREAKER_FAST_DEATH_THRESHOLD
        )
        assert await _wait_until(lambda: "bot1" in trigger._breaker_blocked_until)
        # Owner re-binds: same key, new secret → breaker clears, re-probe now.
        trigger.creds = [_Credential(app_secret="fresh-secret")]
        assert await _wait_until(
            lambda: trigger.loop_starts > trigger.BREAKER_FAST_DEATH_THRESHOLD
        )
        assert "bot1" not in trigger._breaker_blocked_until
    finally:
        await trigger.stop()


@pytest.mark.asyncio
async def test_removed_credential_purges_breaker_state(db_client):
    trigger = _BreakerTrigger()
    await trigger.start(db_client)
    try:
        assert await _wait_until(lambda: "bot1" in trigger._breaker_blocked_until)
        trigger.creds = []
        assert await _wait_until(
            lambda: "bot1" not in trigger._breaker_blocked_until
            and "bot1" not in trigger._breaker_fast_deaths
        )
    finally:
        await trigger.stop()
