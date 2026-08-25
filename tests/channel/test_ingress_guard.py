"""
@file_name: test_ingress_guard.py
@author:
@date: 2026-08-24
@description: State-machine tests for the message-ingress circuit breaker.

Pins the L2/L3 model from the 2026-08-17 ingress design: a session trips
only when it is BOTH fast AND repetitive, cooldowns escalate along a fixed
schedule, expiry buys exactly one half-open probe, and a session that
behaves settles back to closed.

Timing style follows ``test_credential_breaker.py``: no sleeping and no
fake clock patched into asyncio. ``IngressGuard.admit`` takes an explicit
``now``, so every temporal assertion is arithmetic on a fixed base instant.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from xyz_agent_context.channel.ingress_guard import (
    IngressGuard,
    content_fingerprint,
)

pytestmark = pytest.mark.asyncio


BASE = datetime(2026, 8, 14, 10, 22, 0, tzinfo=timezone.utc)

# Shrunk so a test can cross a threshold in a handful of calls, the same way
# _BreakerTrigger shrinks the credential breaker's constants.
SCHEDULE = (300.0, 1800.0, 7200.0, 86400.0)


def _guard(**kwargs) -> IngressGuard:
    defaults = dict(
        window_seconds=600,
        rate_threshold=10,
        dup_ratio_threshold=0.8,
        agent_rate_threshold=5,
        agent_dup_ratio_threshold=0.5,
        schedule_seconds=SCHEDULE,
        recovery_windows=2,
    )
    defaults.update(kwargs)
    return IngressGuard(**defaults)


async def _send(
    guard: IngressGuard,
    *,
    n: int,
    text,
    start: datetime,
    step_seconds: float = 1.0,
    is_agent_peer: bool = False,
    chat_id: str = "room1",
    sender_id: str = "peer1",
):
    """Feed ``n`` messages and return the list of verdicts."""
    out = []
    for i in range(n):
        body = text(i) if callable(text) else text
        out.append(
            await guard.admit(
                agent_id="agt_1",
                channel="narramessenger",
                chat_id=chat_id,
                sender_id=sender_id,
                fingerprint=content_fingerprint(chat_id, sender_id, body),
                is_agent_peer=is_agent_peer,
                now=start + timedelta(seconds=i * step_seconds),
            )
        )
    return out


# ─────────────────────────────────────────────────────────────────────
# Entry condition — rate AND repetition, never either alone
# ─────────────────────────────────────────────────────────────────────

async def test_verbatim_repeat_burst_trips():
    guard = _guard()
    verdicts = await _send(guard, n=12, text="ping", start=BASE)

    assert verdicts[0].admit is True, "the first message must always flow"
    denied = [v for v in verdicts if not v.admit]
    assert denied, "a verbatim repeat burst must trip"
    trip = denied[0]
    assert trip.tier == 1
    assert trip.transition == "tripped"
    assert trip.cooldown_seconds == SCHEDULE[0]


async def test_fast_but_varied_never_trips():
    """A user firing off many DIFFERENT messages is not a loop."""
    guard = _guard()
    verdicts = await _send(guard, n=60, text=lambda i: f"message number {i}", start=BASE)

    assert all(v.admit for v in verdicts)
    assert all(v.tier == 0 for v in verdicts)


async def test_repetitive_but_slow_never_trips():
    """The same "ok" once every few minutes is a quiet human, not a storm."""
    guard = _guard()
    verdicts = await _send(guard, n=40, text="ok", start=BASE, step_seconds=200.0)

    assert all(v.admit for v in verdicts)


async def test_agent_peer_thresholds_are_tighter():
    """A2A rooms are the loop-prone surface, so they trip sooner."""
    human = _guard()
    human_verdicts = await _send(human, n=7, text="ping", start=BASE)
    assert all(v.admit for v in human_verdicts), "7 < human rate_threshold=10"

    agent = _guard()
    agent_verdicts = await _send(agent, n=7, text="ping", start=BASE, is_agent_peer=True)
    assert any(not v.admit for v in agent_verdicts), "7 >= agent rate_threshold=5"


async def test_half_repetition_trips_agent_peer_only():
    """dup_ratio ~0.5 — each body said exactly twice.

    Over the agent bar (0.5), under the human one (0.8): the same traffic
    is a loop worth breaking between two agents and merely a chatty human
    otherwise.
    """
    text = lambda i: f"body {i // 2}"  # noqa: E731 — every body sent twice

    human = _guard()
    assert all(v.admit for v in await _send(human, n=10, text=text, start=BASE))

    agent = _guard()
    assert any(
        not v.admit for v in await _send(agent, n=10, text=text, start=BASE, is_agent_peer=True)
    )


async def test_two_bodies_alternating_forever_is_a_loop_for_everyone():
    """Ping-pong does not become innocent by having two lines instead of
    one: 20 messages drawn from 2 distinct bodies scores 0.9, over even
    the human bar."""
    guard = _guard()
    verdicts = await _send(guard, n=20, text=lambda i: f"body {i % 2}", start=BASE)
    assert any(not v.admit for v in verdicts)


async def test_empty_fingerprints_are_not_duplicates_of_each_other():
    """Caption-less uploads produce no fingerprint; they must not read as a
    verbatim repeat storm (the same carve-out the empty-content guard makes
    for attachment_refs)."""
    guard = _guard()
    verdicts = []
    for i in range(30):
        verdicts.append(
            await guard.admit(
                agent_id="agt_1",
                channel="slack",
                chat_id="room1",
                sender_id="peer1",
                fingerprint="",
                is_agent_peer=False,
                now=BASE + timedelta(seconds=i),
            )
        )
    assert all(v.admit for v in verdicts)


async def test_sessions_are_isolated():
    """One noisy conversation must not isolate a quiet neighbour."""
    guard = _guard()
    await _send(guard, n=12, text="ping", start=BASE, chat_id="noisy")
    quiet = await _send(guard, n=3, text="hello", start=BASE, chat_id="quiet")
    assert all(v.admit for v in quiet)


# ─────────────────────────────────────────────────────────────────────
# Cooldown, escalation, half-open probe
# ─────────────────────────────────────────────────────────────────────

async def test_messages_during_cooldown_are_suppressed_and_counted():
    guard = _guard()
    await _send(guard, n=12, text="ping", start=BASE)

    later = BASE + timedelta(seconds=60)
    verdicts = await _send(guard, n=5, text="ping", start=later)
    assert all(not v.admit for v in verdicts)
    assert all(v.reason == "cooling" for v in verdicts)
    assert verdicts[-1].suppressed > verdicts[0].suppressed, "each drop is counted"


async def test_cooldown_expiry_admits_exactly_one_probe():
    """A NEW message after the cooldown is the probe.

    (Originally this test sent the same "ping" the session had been
    reciting and still expected a probe — which is exactly how it missed
    that the probe was admitted without being judged. A repeated probe now
    re-trips; see the half-open section below.)
    """
    guard = _guard()
    await _send(guard, n=12, text="ping", start=BASE)

    # Well past the cooldown: it starts at the TRIPPING message, which is
    # some seconds into the burst, not at BASE.
    after = BASE + timedelta(seconds=SCHEDULE[0] + 60)
    probe = (await _send(guard, n=1, text="a new subject", start=after))[0]
    assert probe.admit is True
    assert probe.transition == "probe"
    assert probe.reason == "cooldown_expired"
    assert probe.tier == 1, "the probe does NOT forgive the escalation memory"


async def test_reoffending_after_probe_lands_on_the_next_tier():
    guard = _guard()
    await _send(guard, n=12, text="ping", start=BASE)

    after = BASE + timedelta(seconds=SCHEDULE[0] + 60)
    verdicts = await _send(guard, n=12, text="ping", start=after)
    escalated = [v for v in verdicts if v.transition == "escalated"]
    assert escalated, "a session that immediately re-offends must escalate"
    assert escalated[0].tier == 2
    assert escalated[0].cooldown_seconds == SCHEDULE[1]


async def test_escalation_plateaus_at_the_last_step():
    guard = _guard()
    now = BASE
    for expected_cooldown in SCHEDULE + (SCHEDULE[-1], SCHEDULE[-1]):
        verdicts = await _send(guard, n=12, text="ping", start=now)
        trip = [v for v in verdicts if v.transition in ("tripped", "escalated")][0]
        assert trip.cooldown_seconds == expected_cooldown
        now = now + timedelta(seconds=expected_cooldown + 60)


async def test_good_behaviour_decays_the_tier_back_to_closed():
    guard = _guard()
    await _send(guard, n=12, text="ping", start=BASE)

    now = BASE + timedelta(seconds=SCHEDULE[0] + 1)
    # Two clean windows (recovery_windows=2) of ordinary, varied traffic.
    for _ in range(6):
        await _send(guard, n=1, text=lambda i, t=now: f"hello {t}", start=now)
        now = now + timedelta(seconds=300)

    final = (await _send(guard, n=1, text="anything new", start=now))[0]
    assert final.admit is True
    assert final.tier == 0, "a recovered session must forget its escalation"


# ─────────────────────────────────────────────────────────────────────
# Durability — a cooldown must outlive the process
# ─────────────────────────────────────────────────────────────────────

async def test_cooldown_survives_a_fresh_guard(db_client):
    from xyz_agent_context.repository import ChannelIngressBreakerRepository

    repo = ChannelIngressBreakerRepository(db_client)
    guard = _guard(repo=repo)
    await _send(guard, n=12, text="ping", start=BASE)

    # A redeploy: brand-new guard, same durable store, still inside cooldown.
    reborn = _guard(repo=repo)
    verdict = (await _send(reborn, n=1, text="ping", start=BASE + timedelta(seconds=30)))[0]
    assert verdict.admit is False
    assert verdict.reason == "cooling"
    assert verdict.tier == 1


async def test_tier_transitions_are_written_through(db_client):
    from xyz_agent_context.repository import ChannelIngressBreakerRepository

    repo = ChannelIngressBreakerRepository(db_client)
    guard = _guard(repo=repo)
    await _send(guard, n=12, text="ping", start=BASE)

    rows = await repo.find_open()
    assert len(rows) == 1
    assert rows[0].tier == 1
    assert rows[0].channel == "narramessenger"
    assert rows[0].agent_id == "agt_1"
    assert rows[0].cooldown_until is not None


async def test_a_dead_repository_fails_open(db_client):
    """The guard is not an authorization gate. If its durable store is
    unreachable, traffic flows — the in-memory half still works."""

    class _DeadRepo:
        async def get(self, session_key):
            raise RuntimeError("db down")

        async def upsert_state(self, session_key, updates):
            raise RuntimeError("db down")

    guard = _guard(repo=_DeadRepo())
    verdict = (await _send(guard, n=1, text="hello", start=BASE))[0]
    assert verdict.admit is True

    # ...and the memory-only breaker still trips.
    assert any(not v.admit for v in await _send(guard, n=12, text="ping", start=BASE))


# ─────────────────────────────────────────────────────────────────────
# Observability surface
# ─────────────────────────────────────────────────────────────────────

async def test_open_session_count_is_a_standing_state():
    guard = _guard()
    assert guard.open_session_count() == 0
    await _send(guard, n=12, text="ping", start=BASE)
    assert guard.open_session_count() == 1


async def test_verdict_carries_the_evidence_for_the_audit_row():
    guard = _guard()
    verdicts = await _send(guard, n=12, text="ping", start=BASE)
    trip = [v for v in verdicts if v.transition == "tripped"][0]
    assert trip.window_count >= 10
    assert trip.dup_ratio >= 0.8
    assert trip.session_key == "agt_1|narramessenger|room1|peer1", (
        "the session key is per-AGENT — one trigger serves every credential"
    )


async def test_content_fingerprint_is_stable_and_scoped():
    a = content_fingerprint("room", "sender", "hello")
    assert a == content_fingerprint("room", "sender", "hello")
    assert a != content_fingerprint("room", "other", "hello")
    assert a != content_fingerprint("other", "sender", "hello")
    assert content_fingerprint("room", "sender", "") == ""


# ─────────────────────────────────────────────────────────────────────
# Memory: a long-running process must not accumulate strangers
# ─────────────────────────────────────────────────────────────────────

async def test_idle_sessions_do_not_accumulate_forever():
    """Every conversation the guard has ever seen used to keep a
    _SessionState (with its deque) for the life of the process. Trigger
    processes are designed to run for days — binding rule #14 makes
    "only matters after 200 hours" not a defence."""
    guard = _guard()
    for i in range(2500):
        await guard.admit(
            agent_id="agt_1",
            channel="slack",
            chat_id=f"C{i}",
            sender_id=f"U{i}",
            fingerprint=content_fingerprint(f"C{i}", f"U{i}", "hi"),
            now=BASE + timedelta(seconds=i),
        )
    assert len(guard._sessions) < 2500, "one-time senders must not be retained"


async def test_pruning_never_drops_a_cooling_session():
    """The whole point of the durable tier is not forgetting. Pruning
    memory must not become a back door that forgets it early."""
    guard = _guard()
    await _send(guard, n=12, text="ping", start=BASE, chat_id="noisy")
    during = BASE + timedelta(seconds=60)
    assert guard.cooling_session_count(during) == 1

    guard.prune_idle(BASE + timedelta(days=7))
    assert guard.cooling_session_count(during) == 1, "a live cooldown is not idle"
    assert guard.open_session_count() == 1


async def test_pruning_never_drops_escalation_memory():
    guard = _guard()
    await _send(guard, n=12, text="ping", start=BASE)

    # Long after the cooldown lapsed, but the tier is what we promised to keep.
    guard.prune_idle(BASE + timedelta(days=30))
    assert guard.open_session_count() == 1


async def test_pruning_keeps_a_live_window():
    """Mid-conversation state must survive a sweep that happens to land
    between two messages."""
    guard = _guard()
    await _send(guard, n=3, text=lambda i: f"msg {i}", start=BASE)
    guard.prune_idle(BASE + timedelta(seconds=10))
    assert len(guard._sessions) == 1


async def test_a_pruned_session_reloads_its_durable_state(db_client):
    """Dropping a closed session is only lossless because the DB row
    survives and is re-read lazily."""
    from xyz_agent_context.repository import ChannelIngressBreakerRepository

    repo = ChannelIngressBreakerRepository(db_client)
    guard = _guard(repo=repo)
    await _send(guard, n=12, text="ping", start=BASE)

    # Force the in-memory copy out, keeping the durable one.
    guard._sessions.clear()

    verdict = (await _send(guard, n=1, text="ping", start=BASE + timedelta(seconds=30)))[0]
    assert verdict.admit is False
    assert verdict.tier == 1


# ─────────────────────────────────────────────────────────────────────
# Multi-agent fan-out — one trigger serves EVERY bound credential
# ─────────────────────────────────────────────────────────────────────

async def _fanout(guard, *, agents, texts, start=BASE, is_agent_peer=True):
    """One room event reaches every member agent's client, so the trigger
    runs _process_message once per agent — see ChannelDedupStore's own
    agent partitioning for the same scenario."""
    verdicts = []
    for k, body in enumerate(texts):
        for agent_id in agents:
            verdicts.append(
                await guard.admit(
                    agent_id=agent_id,
                    channel="narramessenger",
                    chat_id="!room",
                    sender_id="@peer",
                    fingerprint=content_fingerprint("!room", "@peer", body),
                    is_agent_peer=is_agent_peer,
                    now=start + timedelta(seconds=k * 3),
                )
            )
    return verdicts


async def test_fanout_to_many_agents_does_not_fake_a_repeat_storm():
    """THE regression for the missing agent_id in the session key.

    Without it the window counted N x the real traffic and — because the
    fingerprint is also agent-independent — all N copies read as verbatim
    repeats. dup_ratio became 1 - 1/N: a function of how many of OUR
    agents sit in the room, not of what the sender said. Five agents made
    a human's fourth DISTINCT message trip the breaker, and the whole room
    went deaf to them for up to 24h.
    """
    for n_agents in (1, 2, 3, 5):
        guard = _guard()
        agents = [f"agt_{i}" for i in range(n_agents)]
        verdicts = await _fanout(
            guard,
            agents=agents,
            texts=[f"a genuinely different message {k}" for k in range(40)],
        )
        assert all(v.admit for v in verdicts), (
            f"{n_agents} agents in the room turned distinct traffic into a "
            f"repeat storm"
        )
        assert all(v.dup_ratio == 0.0 for v in verdicts), (
            "the duplicate ratio must describe the SENDER, not our headcount"
        )


async def test_each_agent_gets_its_own_session():
    """Two agents, same room, same sender: two independent breakers."""
    guard = _guard()
    keys = {
        (
            await guard.admit(
                agent_id=agent_id,
                channel="narramessenger",
                chat_id="!room",
                sender_id="@peer",
                fingerprint=content_fingerprint("!room", "@peer", "hi"),
                now=BASE,
            )
        ).session_key
        for agent_id in ("agt_a", "agt_b")
    }
    assert len(keys) == 2


async def test_a_real_storm_still_trips_under_fanout():
    """The fix must not buy immunity by making the breaker blind."""
    guard = _guard()
    verdicts = await _fanout(
        guard, agents=["agt_a", "agt_b"], texts=["same line"] * 40
    )
    assert any(not v.admit for v in verdicts)


async def test_one_agent_tripping_does_not_deafen_its_roommates():
    guard = _guard()
    for i in range(30):
        await guard.admit(
            agent_id="agt_noisy",
            channel="narramessenger",
            chat_id="!room",
            sender_id="@peer",
            fingerprint=content_fingerprint("!room", "@peer", "same line"),
            is_agent_peer=True,
            now=BASE + timedelta(seconds=i),
        )
    quiet = await guard.admit(
        agent_id="agt_quiet",
        channel="narramessenger",
        chat_id="!room",
        sender_id="@peer",
        fingerprint=content_fingerprint("!room", "@peer", "same line"),
        is_agent_peer=True,
        now=BASE + timedelta(seconds=31),
    )
    assert quiet.admit is True


# ─────────────────────────────────────────────────────────────────────
# Half-open: the probe is JUDGED, not merely counted
# ─────────────────────────────────────────────────────────────────────

async def test_a_resumed_recital_retrips_on_the_probe_itself():
    """"Exactly one probe" used to be true of the admission and false of
    the consequence: clearing the window meant re-tripping required
    re-earning a whole rate_bar, so each cooldown let 10 (here) full
    pipeline runs through, each producing another outbound message."""
    guard = _guard()
    await _send(guard, n=12, text="ping", start=BASE)

    after = BASE + timedelta(seconds=SCHEDULE[0] + 60)
    verdicts = await _send(guard, n=5, text="ping", start=after)

    admitted = [v for v in verdicts if v.admit]
    assert not admitted, (
        f"{len(admitted)} messages got through a cooldown that had already "
        f"been earned — the fingerprint IS the evidence the recital "
        f"resumed, so no pipeline run is needed to confirm it"
    )
    retrip = verdicts[0]
    assert retrip.transition == "escalated"
    assert retrip.reason == "probe_repeated"
    assert retrip.tier == 2


async def test_a_genuinely_new_message_is_not_punished_after_a_cooldown():
    """A person whose first sentence after a 24h cooldown is real
    conversation must not be re-isolated for it."""
    guard = _guard()
    await _send(guard, n=12, text="ping", start=BASE)

    after = BASE + timedelta(seconds=SCHEDULE[0] + 60)
    probe = (await _send(guard, n=1, text="something completely new", start=after))[0]
    assert probe.admit is True
    assert probe.transition == "probe"

    # ...and normal conversation continues to flow.
    more = await _send(
        guard, n=5, text=lambda i: f"still talking {i}",
        start=after + timedelta(seconds=10),
    )
    assert all(v.admit for v in more)
