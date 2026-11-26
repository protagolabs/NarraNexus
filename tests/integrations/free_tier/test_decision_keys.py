"""
@file_name: test_decision_keys.py
@description: The per-agent decision-key cache in front of quota-api.

One quota-api round trip per agent per process; a key the gateway rejected is
dropped so the next turn re-fetches; a failure to get a key is a None (the
resolver falls through) and quota-api is not re-probed on every turn. The
issuer under test is real; only quota-api is faked (httpx.MockTransport).
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from loguru import logger

from narranexus.platform.integrations.free_tier import decision_keys
from narranexus.platform.integrations.free_tier.decision_keys import (
    DecisionKeyIssuer,
    decision_key_issuer,
)
from narranexus.platform.integrations.free_tier.wallet_client import WalletClient


GATEWAY = "http://litellm:4000/v1"


class _QuotaApi:
    """Fake quota-api: one key per agent (``generation`` bumps it, like an
    admin-key rotation), or a canned failure. Counts calls per agent."""

    def __init__(self, *, status: int = 200, raise_exc: Exception | None = None,
                 delay: float = 0.0):
        self.status = status
        self.raise_exc = raise_exc
        self.delay = delay
        self.generation = 1
        self.calls: list[str] = []

    def key(self, agent_id: str) -> str:
        return f"sk-nxdm-{agent_id}-g{self.generation}"

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        agent_id = json.loads(request.content)["agent_id"]
        self.calls.append(agent_id)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.status != 200:
            return httpx.Response(self.status, json={"detail": "nope"})
        return httpx.Response(200, json={
            "agent_id": agent_id, "api_key": self.key(agent_id),
            "max_budget": 1.0, "budget_duration": "30d",
        })


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _issuer(quota: _QuotaApi, clock: _Clock | None = None) -> DecisionKeyIssuer:
    wallet = WalletClient("http://quota-api", "tok", transport=httpx.MockTransport(quota))
    return DecisionKeyIssuer(wallet, GATEWAY, clock=clock or _Clock())


async def test_many_turns_of_one_agent_cost_one_quota_api_call():
    quota = _QuotaApi()
    issuer = _issuer(quota)

    keys = [await issuer.key_for("agent_a") for _ in range(5)]

    assert quota.calls == ["agent_a"]
    assert {k.api_key for k in keys} == {quota.key("agent_a")}
    assert all(k.base_url == GATEWAY for k in keys)


async def test_two_agents_get_their_own_keys():
    quota = _QuotaApi()
    issuer = _issuer(quota)

    a = await issuer.key_for("agent_a")
    b = await issuer.key_for("agent_b")

    assert (a.api_key, b.api_key) == (quota.key("agent_a"), quota.key("agent_b"))
    assert quota.calls == ["agent_a", "agent_b"]


async def test_a_rejected_key_is_dropped_and_the_next_turn_refetches():
    quota = _QuotaApi()
    issuer = _issuer(quota)
    stale = await issuer.key_for("agent_a")
    quota.generation = 2  # the operator rotated the gateway admin key

    stale.on_rejected()
    fresh = await issuer.key_for("agent_a")

    assert quota.calls == ["agent_a", "agent_a"]
    assert fresh.api_key == quota.key("agent_a") != stale.api_key


async def test_a_late_rejection_of_an_old_key_keeps_the_fresh_one():
    quota = _QuotaApi()
    issuer = _issuer(quota)
    stale = await issuer.key_for("agent_a")
    stale.on_rejected()
    quota.generation = 2
    await issuer.key_for("agent_a")

    stale.on_rejected()  # a slow turn still holding the old key reports late
    await issuer.key_for("agent_a")

    assert quota.calls == ["agent_a", "agent_a"]


@pytest.mark.parametrize(
    "quota",
    [
        _QuotaApi(status=404),
        _QuotaApi(status=502),
        _QuotaApi(status=401),
        _QuotaApi(raise_exc=httpx.ConnectError("refused")),
    ],
    ids=["older_quota_api", "gateway_down", "bad_token", "unreachable"],
)
async def test_a_failure_is_no_key_and_quota_api_is_not_reprobed_until_the_ttl(quota):
    clock = _Clock()
    issuer = _issuer(quota, clock)

    assert await issuer.key_for("agent_a") is None
    assert await issuer.key_for("agent_b") is None, "inside the TTL: not asked"
    assert len(quota.calls) == 1

    clock.now += decision_keys._RETRY_AFTER_S + 1
    assert await issuer.key_for("agent_b") is None
    assert len(quota.calls) == 2, "after the TTL quota-api is asked again"


async def test_a_slow_quota_api_times_out_into_no_key(monkeypatch):
    monkeypatch.setattr(decision_keys, "_FETCH_TIMEOUT_S", 0.05)
    quota = _QuotaApi(delay=5.0)
    issuer = _issuer(quota)

    started = asyncio.get_running_loop().time()
    assert await issuer.key_for("agent_a") is None
    assert asyncio.get_running_loop().time() - started < 1.0


async def test_recovery_after_the_ttl_caches_normally():
    quota = _QuotaApi(status=502)
    clock = _Clock()
    issuer = _issuer(quota, clock)
    assert await issuer.key_for("agent_a") is None

    quota.status = 200
    clock.now += decision_keys._RETRY_AFTER_S + 1
    assert (await issuer.key_for("agent_a")).api_key == quota.key("agent_a")
    await issuer.key_for("agent_a")
    assert quota.calls == ["agent_a", "agent_a"]


async def test_the_cache_is_bounded_least_recently_used_first(monkeypatch):
    monkeypatch.setattr(decision_keys, "_MAX_CACHED_AGENTS", 2)
    quota = _QuotaApi()
    issuer = _issuer(quota)

    for agent in ("a", "b", "a", "c"):  # "a" was used after "b": "b" goes
        await issuer.key_for(agent)
    await issuer.key_for("a")
    await issuer.key_for("b")

    assert quota.calls == ["a", "b", "c", "b"]


async def test_an_empty_agent_id_asks_nobody():
    quota = _QuotaApi()
    assert await _issuer(quota).key_for("") is None
    assert quota.calls == []


async def test_the_key_never_appears_in_logs():
    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), level="DEBUG")
    try:
        quota = _QuotaApi()
        issuer = _issuer(quota)
        key = await issuer.key_for("agent_a")
        key.on_rejected()
        quota.status = 502
        await issuer.key_for("agent_a")
    finally:
        logger.remove(sink)
    assert lines, "the failure must be logged"
    assert not any(key.api_key in line for line in lines)


# ── which deployments have an issuer at all ────────────────────────────────


@pytest.fixture
def fresh_factory():
    decision_key_issuer.cache_clear()
    yield
    decision_key_issuer.cache_clear()


def test_local_mode_has_no_issuer_even_with_wallet_settings(monkeypatch, fresh_factory):
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "local")
    monkeypatch.setenv("FREE_TIER_WALLET_API_URL", "http://quota-api:8110")
    monkeypatch.setenv("FREE_TIER_WALLET_API_TOKEN", "tok")
    assert decision_key_issuer() is None


def test_cloud_without_the_wallet_service_has_no_issuer(monkeypatch, fresh_factory):
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud")
    monkeypatch.delenv("FREE_TIER_WALLET_API_URL", raising=False)
    monkeypatch.delenv("FREE_TIER_WALLET_API_TOKEN", raising=False)
    assert decision_key_issuer() is None


def test_cloud_with_the_wallet_service_sends_to_the_gateway_openai_base(
    monkeypatch, fresh_factory
):
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud")
    monkeypatch.setenv("FREE_TIER_WALLET_API_URL", "http://quota-api:8110")
    monkeypatch.setenv("FREE_TIER_WALLET_API_TOKEN", "tok")
    monkeypatch.setenv("FREE_TIER_GATEWAY_OPENAI_BASE_URL", "http://gw.internal:4000/v1")

    issuer = decision_key_issuer()

    assert isinstance(issuer, DecisionKeyIssuer)
    assert issuer._base_url == "http://gw.internal:4000/v1"
    assert decision_key_issuer() is issuer, "one per process: the cache outlives a turn"
