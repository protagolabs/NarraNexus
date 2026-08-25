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


async def test_warm_start_does_not_resurrect_an_elapsed_cooldown(db_client):
    """Escalation memory is kept; a cooldown that has already run out is
    not re-imposed."""
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
    await guard.warm_start("narramessenger", now=BASE)

    assert guard.open_session_count() == 1, "the tier is remembered"
    assert guard.cooling_session_count() == 0, "the cooldown is over"


async def test_warm_start_survives_a_broken_store(db_client):
    """Startup must not depend on it."""

    class _DeadRepo:
        async def find_open(self, channel=None):
            raise RuntimeError("db down")

    guard = _guard(_DeadRepo())
    assert await guard.warm_start("narramessenger") == 0
