"""
@file_name: test_ingress_breaker_persistence.py
@author:
@date: 2026-08-24
@description: A cooldown must outlive the process that set it.

The credential breaker in ``channel_trigger_base`` is deliberately
memory-only — it describes LIVE subscriber state, and a stopped trigger
must not carry isolation into its next start. The ingress breaker made
the opposite call, for one reason: the 8/14 loop ran 70+ HOURS. Any
redeploy inside that window would have handed a sender we had already
isolated for a day a completely fresh budget, and the incident would have
resumed with the platform believing it had never seen them.

Split of concerns pinned here:
  - sliding-window counts + fingerprints → memory only (they expire in
    ten minutes; a row per inbound message is pure write amplification)
  - tier + cooldown → written through on TRANSITION, read back on the
    first message a new process sees for that key
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from xyz_agent_context.channel.ingress_guard import IngressGuard, content_fingerprint
from xyz_agent_context.repository import ChannelIngressBreakerRepository

pytestmark = pytest.mark.asyncio

BASE = datetime(2026, 8, 14, 10, 22, 0, tzinfo=timezone.utc)
KEY = "agt_1|narramessenger|!room|@agent-liam"  # agent_id is part of the key


def _guard(repo) -> IngressGuard:
    return IngressGuard(
        repo=repo,
        window_seconds=600,
        rate_threshold=10,
        dup_ratio_threshold=0.8,
        schedule_seconds=(300.0, 1800.0, 7200.0, 86400.0),
    )


async def _storm(guard, *, start=BASE, n=12, text="ping"):
    out = []
    for i in range(n):
        out.append(
            await guard.admit(
                agent_id="agt_1",
                channel="narramessenger",
                chat_id="!room",
                sender_id="@agent-liam",
                fingerprint=content_fingerprint("!room", "@agent-liam", text),
                now=start + timedelta(seconds=i),
            )
        )
    return out


async def test_a_restart_does_not_hand_back_a_fresh_budget(db_client):
    repo = ChannelIngressBreakerRepository(db_client)
    await _storm(_guard(repo))

    reborn = _guard(repo)  # the redeploy
    verdict = (await _storm(reborn, start=BASE + timedelta(seconds=30), n=1))[0]

    assert verdict.admit is False
    assert verdict.reason == "cooling"
    assert verdict.tier == 1


async def test_a_restart_after_the_cooldown_admits_a_probe_at_the_kept_tier(db_client):
    repo = ChannelIngressBreakerRepository(db_client)
    await _storm(_guard(repo))

    reborn = _guard(repo)
    verdict = (await _storm(reborn, start=BASE + timedelta(seconds=400), n=1))[0]

    assert verdict.admit is True, "the cooldown has elapsed; probe it"
    assert verdict.tier == 1, (
        "escalation memory must survive the restart too — otherwise a "
        "re-offender restarts at the cheapest cooldown after every deploy"
    )


async def test_escalation_continues_across_a_restart(db_client):
    repo = ChannelIngressBreakerRepository(db_client)
    await _storm(_guard(repo))

    reborn = _guard(repo)
    verdicts = await _storm(reborn, start=BASE + timedelta(seconds=400))
    escalated = [v for v in verdicts if v.transition == "escalated"]

    assert escalated, "a re-offending session must escalate after a restart"
    assert escalated[0].tier == 2
    assert escalated[0].cooldown_seconds == 1800.0, "the NEXT step, not the first"


async def test_only_transitions_are_written(db_client):
    """The hot path must not write. If it ever starts, an inbound storm
    becomes a DB storm — trading one outage for another."""
    repo = ChannelIngressBreakerRepository(db_client)
    writes: list[str] = []
    original = repo.upsert_state

    async def _counting(session_key, updates):
        writes.append(session_key)
        await original(session_key, updates)

    repo.upsert_state = _counting  # type: ignore[method-assign]

    guard = _guard(repo)
    await _storm(guard)                                    # 12 msgs → 1 trip
    await _storm(guard, start=BASE + timedelta(seconds=60), n=50)  # all cooling

    assert len(writes) == 1, (
        f"{len(writes)} durable writes for 62 inbound messages — only tier "
        f"transitions may write"
    )


async def test_a_closed_session_ages_out_but_an_isolated_one_does_not(db_client):
    """Retention must not forget the sessions we promised to remember."""
    repo = ChannelIngressBreakerRepository(db_client)
    await repo.upsert_state("chan|quiet|peer", {"channel": "chan", "tier": 0})
    await repo.upsert_state("chan|noisy|peer", {"channel": "chan", "tier": 3})

    deleted = await repo.cleanup_older_than_days(0)

    assert deleted == 1
    assert await repo.get("chan|quiet|peer") is None
    assert (await repo.get("chan|noisy|peer")).tier == 3


async def test_the_window_itself_is_not_persisted(db_client):
    """Sub-storm traffic leaves no row at all — nothing to clean up, no
    write amplification."""
    repo = ChannelIngressBreakerRepository(db_client)
    guard = _guard(repo)
    for i in range(9):  # under the rate bar
        await guard.admit(
            agent_id="agt_1",
            channel="narramessenger",
            chat_id="!room",
            sender_id="@agent-liam",
            fingerprint=content_fingerprint("!room", "@agent-liam", "ping"),
            now=BASE + timedelta(seconds=i),
        )
    assert await repo.get(KEY) is None


async def test_the_absorbed_count_is_persisted_when_the_isolation_ends(db_client):
    """`suppressed_count` exists to answer "how much did this cooldown
    swallow?". The hot path deliberately never writes, so the probe is the
    only moment that number can reach the DB — writing 0 there (as the
    first version did) left the column stuck at 0 for its whole life.
    """
    repo = ChannelIngressBreakerRepository(db_client)
    guard = _guard(repo)
    await _storm(guard)

    # 50 messages arrive while the conversation is isolated.
    await _storm(guard, start=BASE + timedelta(seconds=60), n=50)

    # A NEW subject is a real probe (a repeat would re-trip on the spot).
    probe = (
        await _storm(
            guard, start=BASE + timedelta(seconds=400), n=1, text="new subject"
        )
    )[0]
    assert probe.transition == "probe"
    assert probe.suppressed >= 50

    row = await repo.get(KEY)
    assert row.suppressed_count == probe.suppressed, (
        "the durable column must agree with what the verdict reported"
    )


async def test_a_fresh_trip_resets_the_absorbed_count(db_client):
    """Each isolation reports its OWN absorption, not a lifetime total."""
    repo = ChannelIngressBreakerRepository(db_client)
    guard = _guard(repo)
    await _storm(guard)
    await _storm(guard, start=BASE + timedelta(seconds=60), n=50)
    await _storm(
        guard, start=BASE + timedelta(seconds=400), n=1, text="new subject"
    )  # probe

    # Re-offend: escalates, and the new isolation starts from zero.
    await _storm(guard, start=BASE + timedelta(seconds=500))
    row = await repo.get(KEY)
    assert row.tier == 2
    assert row.suppressed_count == 0


async def test_an_escalating_probe_still_reports_what_it_absorbed(db_client):
    """The re-trip path must not lose the ending isolation's count.

    ``_trip`` reads ``state.suppressed`` as "what the isolation that just
    ended absorbed". Zeroing it before that call (the first version of the
    half-open re-trip) made the escalation's audit row claim it had
    absorbed nothing.
    """
    repo = ChannelIngressBreakerRepository(db_client)
    guard = _guard(repo)
    await _storm(guard)
    await _storm(guard, start=BASE + timedelta(seconds=60), n=50)

    # Same recital resumes → re-trips on the probe itself.
    retrip = (await _storm(guard, start=BASE + timedelta(seconds=400), n=1))[0]
    assert retrip.transition == "escalated"
    assert retrip.reason == "probe_repeated"
    assert retrip.suppressed >= 50, "the ended isolation's absorption is lost"


async def test_warm_start_restores_the_standing_state_after_a_restart(db_client):
    """`/healthz` and the heartbeat read the IN-MEMORY counts.

    Without a warm start a restarted process holding isolated
    conversations reported `ingress_breaker_open_count: 0` — deploy, and
    the dashboard goes green while those conversations stay deaf. That is
    the L2-health blind spot (incident lesson #4) this design cites as its
    reason for existing.
    """
    repo = ChannelIngressBreakerRepository(db_client)
    await _storm(_guard(repo))

    reborn = _guard(repo)
    assert reborn.open_session_count() == 0, "nothing loaded yet"

    restored = await reborn.warm_start("narramessenger", now=BASE + timedelta(seconds=60))
    assert restored == 1
    assert reborn.open_session_count() == 1
    assert reborn.cooling_session_count(BASE + timedelta(seconds=60)) == 1


async def test_warm_start_skips_a_session_whose_cooldown_has_elapsed(db_client):
    """Preloading is for the OBSERVABILITY surface, which only ever meant
    "currently suppressed" — so a lapsed row is not loaded at all.

    Loading every ``tier > 0`` row (the first version) would have made
    both the footprint and ``open_session_count()`` climb with every
    deploy: retention sweeps ``tier = 0`` exclusively, and a session that
    trips once then goes quiet never gets the ``admit()`` calls tier decay
    needs, so its row lives forever. "Reports 0 after a restart" would
    have become "reports a growing historical total" — equally useless
    for "how many are isolated right now".

    The escalation memory is not lost: ``_load()`` fetches it at the only
    moment it matters, when that key speaks again (asserted below).
    """
    repo = ChannelIngressBreakerRepository(db_client)
    await repo.upsert_state(
        KEY,
        {
            "channel": "narramessenger",
            "agent_id": "agt_1",
            "tier": 2,
            "cooldown_until": BASE - timedelta(days=1),
        },
    )
    guard = _guard(repo)
    restored = await guard.warm_start("narramessenger", now=BASE)

    assert restored == 0
    assert guard.open_session_count() == 0, "a lapsed row is not current state"

    # ...but the tier is still there the moment the conversation resumes.
    await _storm(guard, start=BASE, n=1, text="hello again")
    assert guard.open_session_count() == 1, "escalation memory lazily reloaded"


async def test_warm_start_does_not_inflate_with_history(db_client):
    """A long-lived deployment accumulates trip rows; the standing-state
    count must not accumulate with them."""
    repo = ChannelIngressBreakerRepository(db_client)
    for i in range(20):
        await repo.upsert_state(
            f"agt_{i}|narramessenger|!room|@peer",
            {
                "channel": "narramessenger",
                "agent_id": f"agt_{i}",
                "tier": 1,
                "cooldown_until": BASE - timedelta(days=3),
            },
        )
    # One conversation is genuinely still isolated.
    await repo.upsert_state(
        "agt_live|narramessenger|!room|@peer",
        {
            "channel": "narramessenger",
            "agent_id": "agt_live",
            "tier": 3,
            "cooldown_until": BASE + timedelta(hours=20),
        },
    )

    guard = _guard(repo)
    assert await guard.warm_start("narramessenger", now=BASE) == 1
    assert guard.open_session_count() == 1
    assert guard.cooling_session_count(BASE) == 1


async def test_warm_start_survives_a_broken_store(db_client):
    """Startup must not depend on it."""

    class _DeadRepo:
        async def find_open(self, channel=None, *, cooling_only=False, now=None):
            raise RuntimeError("db down")

    guard = _guard(_DeadRepo())
    assert await guard.warm_start("narramessenger") == 0


async def test_session_key_cannot_overflow_its_column():
    """The key's width must be DERIVED from the DDL, not asserted next to it.

    The first clamp used one number (128) for all four parts, which allows
    4*128 + 3 = 515 — over the 448 the column was sized for, because
    ``channel`` is only VARCHAR(32). The comment above it claimed the
    arithmetic was "true by construction"; nobody had run the
    multiplication. This test runs it, against the registry, so the claim
    cannot drift from the schema again.
    """
    from xyz_agent_context.schema.channel_ingress_breaker_schema import session_key
    from xyz_agent_context.utils.db.schema_registry import varchar_width

    worst = session_key("a" * 4000, "b" * 4000, "c" * 4000, "d" * 4000)
    limit = varchar_width("channel_ingress_breaker", "session_key")
    assert len(worst) <= limit, (
        f"a maximal key is {len(worst)} chars but session_key is "
        f"VARCHAR({limit}) — on a MySQL deployment without strict mode this "
        f"truncates, and two conversations collide on the unique index"
    )

    # And each component stays inside its own column too.
    parts = dict(zip(("agent_id", "channel", "chat_id", "sender_id"), worst.split("|")))
    for name, value in parts.items():
        assert len(value) <= varchar_width("channel_ingress_breaker", name), name


async def test_a_session_that_went_silent_ages_its_tier_down(db_client):
    """Escalation memory must expire with TIME, not only with traffic.

    `_maybe_recover` runs from `admit()`, so it walks a tier down only
    while the session KEEPS TALKING. A session that trips and then goes
    quiet is never visited again: nothing polls, and the durable row is
    read back verbatim. The tier would stand for the life of the row.

    That is the failure `_maybe_recover`'s own docstring names — a
    conversation that misbehaved once carrying a 24h-capable tier forever,
    so the first bad minute a year later costs a day.
    """
    repo = ChannelIngressBreakerRepository(db_client)
    await _storm(_guard(repo))  # trips to tier 1

    guard = _guard(repo)
    step = guard._decay_step_seconds

    # Comes back after enough silence for exactly one step.
    verdict = (
        await _storm(guard, start=BASE + timedelta(seconds=step + 60), n=1)
    )[0]

    assert verdict.admit is True
    assert verdict.tier == 0, (
        "silence past one decay step must have aged the tier down, not "
        f"handed the session back its escalation memory: tier={verdict.tier}"
    )


async def test_ageing_down_to_zero_makes_the_row_sweepable(db_client):
    """The other half of the same defect: `cleanup_older_than_days`
    deliberately sweeps only `tier = 0`, so a session stuck above zero is
    a row nothing can ever reclaim. Ageing must be written back."""
    repo = ChannelIngressBreakerRepository(db_client)
    await _storm(_guard(repo))

    row = await repo.get(KEY)
    assert row is not None and row.tier == 1

    guard = _guard(repo)
    await _storm(guard, start=BASE + timedelta(seconds=guard._decay_step_seconds + 60), n=1)

    row = await repo.get(KEY)
    assert row is not None
    assert row.tier == 0, "the durable row still carries a tier nothing can sweep"
    assert row.last_reason == "aged_out"


async def test_many_silent_steps_age_down_by_many_tiers(db_client):
    """The catch-up is proportional to the silence, not one step per load.

    A row six months old must not need six months of restarts to come
    back to zero.
    """
    repo = ChannelIngressBreakerRepository(db_client)
    guard = _guard(repo)
    await repo.upsert_state(
        KEY,
        {
            "channel": "narramessenger",
            "agent_id": "agt_1",
            "chat_id": "!room",
            "sender_id": "@agent-liam",
            "tier": 3,
            "cooldown_until": None,
            "last_reason": "escalated",
            "tier_changed_at": BASE,
        },
    )

    later = BASE + timedelta(seconds=guard._decay_step_seconds * 3 + 60)
    verdict = (await _storm(guard, start=later, n=1))[0]
    assert verdict.tier == 0, f"three steps of silence, tier={verdict.tier}"


async def test_ageing_never_releases_a_session_still_in_its_cooldown(db_client):
    """Decaying inside the cooldown would end the isolation early — the one
    thing this state exists to hold. The 8/14 loop ran 70+ hours; a reload
    partway through must not shorten the sentence."""
    repo = ChannelIngressBreakerRepository(db_client)
    await _storm(_guard(repo))  # tier 1, cooldown 300s from BASE

    guard = _guard(repo)
    verdict = (await _storm(guard, start=BASE + timedelta(seconds=120), n=1))[0]

    assert verdict.admit is False, "still inside the cooldown"
    assert verdict.tier == 1, "the tier must not have aged while isolated"


async def test_ageing_does_not_run_while_the_isolation_is_still_in_force(db_client):
    """Long cooldowns outlive a decay step, so the guard must be explicit.

    At tier 3 the cooldown is 7200s while a decay step is 1200s. A reload
    two thousand seconds in is therefore both "still isolated" and "past a
    step" — the one timeline where ageing would cut the sentence short.
    The 8/14 loop ran 70+ hours; a redeploy partway through must not
    shorten it.
    """
    repo = ChannelIngressBreakerRepository(db_client)
    guard = _guard(repo)
    await repo.upsert_state(
        KEY,
        {
            "channel": "narramessenger",
            "agent_id": "agt_1",
            "chat_id": "!room",
            "sender_id": "@agent-liam",
            "tier": 3,
            "cooldown_until": BASE + timedelta(seconds=7200),
            "last_reason": "escalated",
            "tier_changed_at": BASE,
        },
    )

    mid = BASE + timedelta(seconds=guard._decay_step_seconds + 100)
    assert mid < BASE + timedelta(seconds=7200), "the timeline must still be cooling"
    verdict = (await _storm(guard, start=mid, n=1))[0]

    assert verdict.admit is False, "still isolated"
    assert verdict.tier == 3, (
        f"ageing released the isolation early: tier={verdict.tier}"
    )


async def test_a_recovery_step_refreshes_the_decay_anchor(db_client):
    """`_maybe_recover` and `_decay_for_silence` must not both bill the
    same silence.

    The anchor is `tier_changed_at`, and a recovery IS a tier change. If a
    recovery left it standing, the next load would count the time already
    paid out by `_maybe_recover` a second time and over-decay — handing a
    re-offender a cheaper cooldown, which is what the tier exists to deny.
    """
    repo = ChannelIngressBreakerRepository(db_client)
    guard = _guard(repo)
    await _storm(guard)  # trips to tier 1 at BASE

    tripped = await repo.get(KEY)
    assert tripped is not None and tripped.tier_changed_at is not None

    # Same instance throughout: the durable catch-up in `_load` only runs
    # on a fresh guard, and it would zero this tier before `_maybe_recover`
    # ever saw it. This is the "kept talking" half of the decay.
    step = guard._decay_step_seconds
    # The first message after the cooldown is the half-open probe; it
    # starts the clean clock rather than earning a step.
    await _storm(guard, start=BASE + timedelta(seconds=400), n=1, text="probe")
    await _storm(guard, start=BASE + timedelta(seconds=400 + step + 60), n=1,
                 text="unrelated")

    row = await repo.get(KEY)
    assert row is not None
    assert row.last_reason == "recovered"
    assert row.tier_changed_at is not None
    assert row.tier_changed_at > tripped.tier_changed_at, (
        "a recovery left the decay anchor at the trip time; the silence it "
        "just consumed would be billed again on the next load"
    )


async def test_find_open_is_bounded_and_says_so_when_it_truncates():
    """`tier > 0` and "still cooling" are both decided in Python, so an
    unbounded query here is a full-table read on every process start.

    The truncation must be loud: a short result reads exactly like a
    complete one, and `warm_start` would report a standing-isolation count
    that is quietly wrong — the same "looks like it covered everything"
    shape a silent cap always has.
    """
    from loguru import logger

    from xyz_agent_context.repository.channel_ingress_breaker_repository import (
        _FIND_OPEN_LIMIT,
    )

    calls = []
    lines = []
    sink = logger.add(lambda m: lines.append(str(m)), level="DEBUG")
    try:

        class _FakeDb:
            async def get(self, table, filters=None, **kwargs):
                calls.append(kwargs)
                return [
                    {
                        "session_key": f"k{i}",
                        "channel": "slack",
                        "agent_id": "a",
                        "chat_id": "c",
                        "sender_id": "s",
                        "tier": 1,
                        "cooldown_until": None,
                        "suppressed_count": 0,
                        "last_reason": None,
                        "last_tripped_at": None,
                        "tier_changed_at": None,
                        "created_at": None,
                        "updated_at": None,
                    }
                    for i in range(_FIND_OPEN_LIMIT + 50)
                ]

        repo = ChannelIngressBreakerRepository(_FakeDb())
        rows = await repo.find_open("slack")
    finally:
        logger.remove(sink)

    assert calls and calls[0].get("limit") is not None, (
        "find_open issued an unbounded read"
    )
    assert len(rows) == _FIND_OPEN_LIMIT, f"cap not applied: {len(rows)}"
    assert any("skipping the rest" in ln for ln in lines), (
        f"the cap bit without saying so: {lines}"
    )
