"""
@file_name: test_decision_keys.py
@description: The per-agent decision-key cache in front of quota-api.

One quota-api round trip per agent per process. A key the gateway rejected
keeps being sent (a revoked agent never falls through to an uncapped
credential) and is re-checked at most once per interval. A service failure
pauses lookups for uncached agents; a per-agent refusal is remembered for that
agent only. The
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
                 delay: float = 0.0, refuse: frozenset[str] = frozenset()):
        self.status = status
        #: Per-agent extra latency, to order concurrent answers.
        self.slow: dict[str, float] = {}
        self.refuse = set(refuse)
        self.raise_exc = raise_exc
        self.delay = delay
        self.generation = 1
        self.base_url = GATEWAY
        self.calls: list[str] = []

    def key(self, agent_id: str) -> str:
        return f"sk-nxdm-{agent_id}-g{self.generation}"

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        agent_id = json.loads(request.content)["agent_id"]
        self.calls.append(agent_id)
        if self.delay or agent_id in self.slow:
            await asyncio.sleep(self.delay + self.slow.get(agent_id, 0.0))
        if self.raise_exc is not None:
            raise self.raise_exc
        if agent_id in self.refuse:
            return httpx.Response(422, json={"detail": "bad agent_id"})
        if self.status != 200:
            return httpx.Response(self.status, json={"detail": "nope"})
        return httpx.Response(200, json={
            "agent_id": agent_id, "api_key": self.key(agent_id),
            **({"base_url": self.base_url} if self.base_url is not None else {}),
            "max_budget": 1.0, "budget_duration": "30d",
        })


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _issuer(quota: _QuotaApi, clock: _Clock | None = None) -> DecisionKeyIssuer:
    wallet = WalletClient("http://quota-api", "tok", transport=httpx.MockTransport(quota))
    return DecisionKeyIssuer(wallet, clock=clock or _Clock())


async def test_many_turns_of_one_agent_cost_one_quota_api_call():
    quota = _QuotaApi()
    issuer = _issuer(quota)

    keys = [await issuer.key_for("agent_a") for _ in range(5)]

    assert quota.calls == ["agent_a"]
    assert {k.api_key for k in keys} == {quota.key("agent_a")}
    assert all(k.base_url == GATEWAY for k in keys)


async def test_the_key_goes_where_quota_api_says_not_to_the_configured_llm_gateway(
    monkeypatch,
):
    """The configured LLM gateway is the sandbox proxy, which refuses the
    decision route; only quota-api's base_url reaches the gateway itself."""
    monkeypatch.setenv("FREE_TIER_GATEWAY_OPENAI_BASE_URL", "http://llm-gateway:4000/v1")
    quota = _QuotaApi()
    quota.base_url = "http://litellm:4000/v1"

    key = await _issuer(quota).key_for("agent_a")

    assert key.base_url == "http://litellm:4000/v1"


async def test_a_reply_without_base_url_is_no_key_and_pauses_quota_api():
    quota = _QuotaApi()
    quota.base_url = None  # a quota-api that predates the field
    clock = _Clock()
    issuer = _issuer(quota, clock)

    assert await issuer.key_for("agent_a") is None
    assert await issuer.key_for("agent_b") is None
    assert quota.calls == ["agent_a"], "service-level: every uncached agent waits"


@pytest.mark.parametrize("bad_key", ["sk-nxdm-has space", "sk-nxdm-\n", "sk-nxdm-\u00e9"])
async def test_an_unsendable_key_is_no_key_and_pauses_quota_api(bad_key):
    """The resolver would silently drop such a key and put the agent on an
    uncapped credential; the issuer treats the reply as unavailable instead."""
    quota = _QuotaApi()
    quota.key = lambda agent_id: bad_key
    issuer = _issuer(quota, _Clock())

    assert await issuer.key_for("agent_a") is None
    assert await issuer.key_for("agent_b") is None
    assert quota.calls == ["agent_a"], "service-level: every uncached agent waits"


async def test_a_moved_gateway_with_the_same_key_is_picked_up_on_recheck():
    quota = _QuotaApi()
    clock = _Clock()
    issuer = _issuer(quota, clock)
    old = await issuer.key_for("agent_a")
    old.on_rejected()
    quota.base_url = "http://gateway-2:4000/v1"
    clock.now += decision_keys._RETRY_AFTER_S + 1

    moved = await issuer.key_for("agent_a")

    assert moved.api_key == old.api_key
    assert moved.base_url == "http://gateway-2:4000/v1"


async def test_two_agents_get_their_own_keys():
    quota = _QuotaApi()
    issuer = _issuer(quota)

    a = await issuer.key_for("agent_a")
    b = await issuer.key_for("agent_b")

    assert (a.api_key, b.api_key) == (quota.key("agent_a"), quota.key("agent_b"))
    assert quota.calls == ["agent_a", "agent_b"]


async def test_a_rejected_key_keeps_being_sent_and_quota_api_is_not_asked():
    """A blocked agent must never fall through to an uncapped credential, and
    must not cost a quota-api call per turn — even while quota-api is paused."""
    quota = _QuotaApi()
    clock = _Clock()
    issuer = _issuer(quota, clock)
    key = await issuer.key_for("agent_a")
    quota.status = 502
    assert await issuer.key_for("agent_b") is None  # trips the service pause

    key.on_rejected()
    for _ in range(3):
        again = await issuer.key_for("agent_a")
        assert again is not None and again.api_key == key.api_key
    assert quota.calls == ["agent_a", "agent_b"]


async def test_after_the_interval_a_rejected_key_is_rechecked_once():
    quota = _QuotaApi()
    clock = _Clock()
    issuer = _issuer(quota, clock)
    key = await issuer.key_for("agent_a")
    key.on_rejected()
    clock.now += decision_keys._RETRY_AFTER_S + 1

    same = await issuer.key_for("agent_a")
    await issuer.key_for("agent_a")
    clock.now += decision_keys._RETRY_AFTER_S + 1
    await issuer.key_for("agent_a")

    assert same.api_key == key.api_key, "the same key is kept"
    assert quota.calls == ["agent_a", "agent_a"], (
        "re-checked once, then trusted again: no further re-check intervals"
    )


async def test_an_over_age_key_is_rechecked_once_and_keeps_serving():
    """A blocked-then-unblocked or re-issued key is noticed without a 401."""
    quota = _QuotaApi()
    clock = _Clock()
    issuer = _issuer(quota, clock)
    old = await issuer.key_for("agent_a")
    clock.now += decision_keys._MAX_KEY_AGE_S - 1
    await issuer.key_for("agent_a")
    assert quota.calls == ["agent_a"], "not before the age is reached"

    quota.generation = 2  # quota-api re-issued the key
    clock.now += 2
    fresh = await issuer.key_for("agent_a")
    await issuer.key_for("agent_a")

    assert fresh.api_key == quota.key("agent_a") != old.api_key
    assert quota.calls == ["agent_a", "agent_a"], "re-checked once, then a full age"


async def test_an_unsendable_answer_on_recheck_keeps_the_old_key():
    """The check guards the re-check too: a bad answer never replaces a good key."""
    quota = _QuotaApi()
    clock = _Clock()
    issuer = _issuer(quota, clock)
    old = await issuer.key_for("agent_a")

    quota.key = lambda agent_id: "sk-nxdm-has space"
    clock.now += decision_keys._MAX_KEY_AGE_S + 1
    again = await issuer.key_for("agent_a")

    assert again.api_key == old.api_key
    assert await issuer.key_for("agent_b") is None
    assert quota.calls == ["agent_a", "agent_a"], "service-level: agent_b is not asked"


class _ScriptedQuotaApi:
    """Answers each call from a script of (delay, status, key generation)."""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[str] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        agent_id = json.loads(request.content)["agent_id"]
        self.calls.append(agent_id)
        delay, status, generation = self.script.pop(0)
        await asyncio.sleep(delay)
        if status != 200:
            return httpx.Response(status, json={"detail": "nope"})
        return httpx.Response(200, json={
            "agent_id": agent_id, "api_key": f"sk-nxdm-{agent_id}-g{generation}",
            "base_url": GATEWAY,
        })


@pytest.mark.parametrize("failure_status", [502, 422], ids=["unavailable", "refused"])
async def test_a_slow_failed_recheck_does_not_overwrite_a_fresher_key(failure_status):
    """Two turns hit the over-age entry: the fast one stores the re-issued
    key, the slow one then fails — it must not write the old key back."""
    quota = _ScriptedQuotaApi([
        (0.0, 200, 1),               # first fetch
        (0.05, failure_status, 0),   # turn A: slow failure
        (0.0, 200, 2),               # turn B: fast success, re-issued key
    ])
    clock = _Clock()
    wallet = WalletClient("http://quota-api", "tok", transport=httpx.MockTransport(quota))
    issuer = DecisionKeyIssuer(wallet, clock=clock)
    await issuer.key_for("agent_a")
    clock.now += decision_keys._MAX_KEY_AGE_S + 1

    await asyncio.gather(issuer.key_for("agent_a"), issuer.key_for("agent_a"))
    now = await issuer.key_for("agent_a")

    assert now.api_key == "sk-nxdm-agent_a-g2"


async def test_an_over_age_key_whose_recheck_fails_keeps_serving():
    quota = _QuotaApi()
    clock = _Clock()
    issuer = _issuer(quota, clock)
    key = await issuer.key_for("agent_a")
    quota.status = 502
    clock.now += decision_keys._MAX_KEY_AGE_S + 1

    kept = await issuer.key_for("agent_a")
    await issuer.key_for("agent_a")

    assert kept is not None and kept.api_key == key.api_key, "never falls through"
    assert quota.calls == ["agent_a", "agent_a"], "then waits one retry interval"
    clock.now += decision_keys._RETRY_AFTER_S + 1
    await issuer.key_for("agent_a")
    assert len(quota.calls) == 3


async def test_an_over_age_key_is_not_rechecked_while_quota_api_is_paused():
    quota = _QuotaApi()
    clock = _Clock()
    issuer = _issuer(quota, clock)
    key = await issuer.key_for("agent_a")
    clock.now += decision_keys._MAX_KEY_AGE_S - 10
    quota.status = 502
    assert await issuer.key_for("agent_b") is None  # pause past agent_a's age
    quota.status = 200
    clock.now += 20  # over age, pause still on

    kept = await issuer.key_for("agent_a")

    assert kept.api_key == key.api_key
    assert quota.calls == ["agent_a", "agent_b"], "no per-agent call while paused"


async def test_no_recheck_while_quota_api_is_paused():
    quota = _QuotaApi()
    clock = _Clock()
    issuer = _issuer(quota, clock)
    key = await issuer.key_for("agent_a")
    key.on_rejected()  # re-check due one interval from now
    clock.now += decision_keys._RETRY_AFTER_S / 2
    quota.status = 502
    assert await issuer.key_for("agent_b") is None  # pause until 1.5 intervals
    quota.status = 200
    clock.now += decision_keys._RETRY_AFTER_S * 3 / 4  # re-check due, pause not over

    kept = await issuer.key_for("agent_a")

    assert kept.api_key == key.api_key
    assert quota.calls == ["agent_a", "agent_b"], "the pause also holds re-checks"


async def test_a_rotated_key_replaces_the_rejected_one_after_the_interval():
    quota = _QuotaApi()
    clock = _Clock()
    issuer = _issuer(quota, clock)
    stale = await issuer.key_for("agent_a")
    quota.generation = 2  # the operator rotated the gateway admin key
    stale.on_rejected()
    assert (await issuer.key_for("agent_a")).api_key == stale.api_key
    clock.now += decision_keys._RETRY_AFTER_S / 2
    stale.on_rejected()  # the agent's next turn is refused too

    clock.now += decision_keys._RETRY_AFTER_S / 2 + 1
    fresh = await issuer.key_for("agent_a")

    assert fresh.api_key == quota.key("agent_a") != stale.api_key, (
        "a repeated 401 must not postpone the re-check"
    )


@pytest.mark.parametrize(
    "failure", ["unavailable", "refused"],
)
async def test_a_failed_recheck_keeps_the_rejected_key_for_another_interval(failure):
    quota = _QuotaApi()
    clock = _Clock()
    issuer = _issuer(quota, clock)
    key = await issuer.key_for("agent_a")
    key.on_rejected()
    clock.now += decision_keys._RETRY_AFTER_S + 1
    if failure == "unavailable":
        quota.status = 502
    else:
        quota.refuse.add("agent_a")

    kept = await issuer.key_for("agent_a")
    await issuer.key_for("agent_a")

    assert kept is not None and kept.api_key == key.api_key
    assert quota.calls == ["agent_a", "agent_a"], "not re-asked inside the interval"


async def test_a_late_rejection_of_an_old_key_does_not_mark_the_fresh_one():
    quota = _QuotaApi()
    clock = _Clock()
    issuer = _issuer(quota, clock)
    stale = await issuer.key_for("agent_a")
    stale.on_rejected()
    quota.generation = 2
    clock.now += decision_keys._RETRY_AFTER_S + 1
    await issuer.key_for("agent_a")  # picks up the rotated key

    stale.on_rejected()  # a slow turn still holding the old key reports late
    clock.now += decision_keys._RETRY_AFTER_S + 1
    await issuer.key_for("agent_a")

    assert quota.calls == ["agent_a", "agent_a"]


async def test_a_per_agent_refusal_does_not_pause_other_agents():
    quota = _QuotaApi(refuse=frozenset({"bad-id"}))
    clock = _Clock()
    issuer = _issuer(quota, clock)

    assert await issuer.key_for("bad-id") is None
    assert (await issuer.key_for("agent_b")).api_key == quota.key("agent_b")
    assert await issuer.key_for("bad-id") is None
    assert quota.calls == ["bad-id", "agent_b"], "the refused agent is not re-probed"

    clock.now += decision_keys._RETRY_AFTER_S + 1
    quota.refuse.clear()
    assert (await issuer.key_for("bad-id")).api_key == quota.key("bad-id")


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
async def test_a_service_failure_pauses_every_uncached_agent_until_the_ttl(quota):
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


async def test_a_recheck_replacing_an_evicted_entry_keeps_the_bound(monkeypatch):
    monkeypatch.setattr(decision_keys, "_MAX_CACHED_AGENTS", 2)
    quota = _QuotaApi()
    clock = _Clock()
    issuer = _issuer(quota, clock)
    stale = await issuer.key_for("a")
    stale.on_rejected()
    quota.generation = 2
    quota.slow["a"] = 0.05
    clock.now += decision_keys._RETRY_AFTER_S + 1

    # "b" and "c" evict "a" while its re-check is in flight.
    await asyncio.gather(issuer.key_for("a"), issuer.key_for("b"), issuer.key_for("c"))

    assert len(issuer._entries) <= 2


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
        quota.status = 502
        await issuer.key_for("agent_b")
    finally:
        logger.remove(sink)
    assert lines, "the failure must be logged"
    assert not any(key.api_key in line for line in lines)


# ── which deployments have an issuer at all ────────────────────────────────


# The repo-wide `_no_real_decision_key_issuer` fixture clears the factory's
# cache and the wallet variables around every test.


def test_local_mode_has_no_issuer_even_with_wallet_settings(monkeypatch):
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "local")
    monkeypatch.setenv("FREE_TIER_WALLET_API_URL", "http://quota-api:8110")
    monkeypatch.setenv("FREE_TIER_WALLET_API_TOKEN", "tok")
    assert decision_key_issuer() is None


def test_cloud_without_the_wallet_service_has_no_issuer(monkeypatch):
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud")
    monkeypatch.delenv("FREE_TIER_WALLET_API_URL", raising=False)
    monkeypatch.delenv("FREE_TIER_WALLET_API_TOKEN", raising=False)
    assert decision_key_issuer() is None


def test_cloud_with_the_wallet_service_has_one_issuer_per_process(monkeypatch):
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud")
    monkeypatch.setenv("FREE_TIER_WALLET_API_URL", "http://quota-api:8110")
    monkeypatch.setenv("FREE_TIER_WALLET_API_TOKEN", "tok")

    issuer = decision_key_issuer()

    assert isinstance(issuer, DecisionKeyIssuer)
    assert decision_key_issuer() is issuer, "one per process: the cache outlives a turn"
