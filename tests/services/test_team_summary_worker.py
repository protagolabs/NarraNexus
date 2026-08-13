"""
@file_name: test_team_summary_worker.py
@author: NarraNexus
@date: 2026-08-10
@description: The auto-summary — the one part of the bulletin nobody asked for
by hand, and therefore the one that must never make things worse.

Its output lands in every member's every team turn, so a bad summary is not a
bad row in a table: it is a paragraph of machine guesswork prepended to every
reply the team makes until it is replaced. That asymmetry drives every rule
pinned here.

  * A failed summarisation KEEPS the previous one. Blanking on failure would
    turn one transient provider hiccup into the loss of the only shared view of
    progress, and "no summary" is indistinguishable to the reader from "the
    team has made no progress".
  * It never blocks or force-stops anything (iron rule #14). It is
    opportunistic background work; a team turn does not wait for it and is
    never cut short by it.
  * It is bounded to its own budget, separately from the user's rules
    (iron rule #16's spirit: the cost of automation is paid by the automation).
  * "Nothing has happened" produces no LLM call at all. A worker that
    summarises an unchanged room every minute burns the user's tokens to
    rewrite the same paragraph.

Triggers are computed from the messages themselves rather than a counter
column. A counter is a second source of truth that drifts the first time a
message is deleted or a wipe runs; `idx_bus_msg_channel_time` makes the live
count an indexed lookup, so there is nothing to keep in sync.
"""

from __future__ import annotations

import pytest

from xyz_agent_context.repository.team_bulletin_repository import (
    TeamBulletinRepository,
)
from xyz_agent_context.services.team_summary_worker import TeamSummaryWorker

OWNER = "user_1"
TEAM = "team_1"
CHANNEL = "ch_team_1"


def _ts(i: int) -> str:
    """Distinct, ordered timestamps.

    The default ``datetime('now')`` has SECOND granularity, so a loop of
    inserts lands every message in the same second and the watermark's
    ``created_at >`` comparison then excludes all of them. Real team messages
    are seconds-to-minutes apart (each is an agent turn), but a test that
    relies on insert-time defaults is relying on sub-second resolution the
    database does not have.
    """
    return f"2026-08-10 12:{i // 60:02d}:{i % 60:02d}"


async def _seed_room(db, *, messages=0, offset=0):
    await db.insert("teams", {"team_id": TEAM, "owner_user_id": OWNER, "name": "T"})
    await db.insert("team_members", {"team_id": TEAM, "agent_id": "agent_a"})
    await db.insert(
        "bus_channels",
        {
            "channel_id": CHANNEL,
            "channel_type": "group",
            "created_by": f"team_{TEAM}",
            "name": "T",
        },
    )
    for i in range(messages):
        await db.insert(
            "bus_messages",
            {
                "message_id": f"m{i}",
                "channel_id": CHANNEL,
                "from_agent": "agent_a",
                "content": f"working on step {i}",
                "msg_type": "text",
                "created_at": _ts(offset + i),
            },
        )


def _worker(db, *, summary="the team shipped the parser"):
    w = TeamSummaryWorker(db)
    calls = []

    async def fake_llm(*, team_id, transcript, bearer=""):
        calls.append({"team_id": team_id, "transcript": transcript})
        if isinstance(summary, Exception):
            raise summary
        return summary

    w._summarise = fake_llm
    w.calls = calls
    return w


# ── when it runs ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_busy_room_gets_summarised(db_client):
    await _seed_room(db_client, messages=TeamSummaryWorker.MESSAGE_THRESHOLD)
    w = _worker(db_client)

    await w.run_once()

    assert (await TeamBulletinRepository(db_client).get_summary(TEAM)) is not None


@pytest.mark.asyncio
async def test_a_quiet_room_is_not_summarised(db_client):
    """Below the threshold there is nothing worth spending a call on."""
    await _seed_room(db_client, messages=2)
    w = _worker(db_client)

    await w.run_once()

    assert w.calls == []
    assert await TeamBulletinRepository(db_client).get_summary(TEAM) is None


@pytest.mark.asyncio
async def test_an_empty_room_costs_nothing(db_client):
    await _seed_room(db_client, messages=0)
    w = _worker(db_client)

    await w.run_once()

    assert w.calls == []


@pytest.mark.asyncio
async def test_an_unchanged_room_is_not_resummarised(db_client):
    """The regression that would quietly bill the user forever: re-running with
    no new messages must not rewrite the same paragraph."""
    await _seed_room(db_client, messages=TeamSummaryWorker.MESSAGE_THRESHOLD)
    w = _worker(db_client)

    await w.run_once()
    first_calls = len(w.calls)
    await w.run_once()

    assert len(w.calls) == first_calls


@pytest.mark.asyncio
async def test_new_messages_after_a_summary_trigger_another(db_client):
    await _seed_room(db_client, messages=TeamSummaryWorker.MESSAGE_THRESHOLD)
    w = _worker(db_client)
    await w.run_once()

    for i in range(TeamSummaryWorker.MESSAGE_THRESHOLD):
        await db_client.insert(
            "bus_messages",
            {
                "message_id": f"later{i}",
                "channel_id": CHANNEL,
                "from_agent": "agent_a",
                "content": "more work",
                "msg_type": "text",
                "created_at": _ts(500 + i),
            },
        )

    await w.run_once()
    assert len(w.calls) == 2


# ── failure must not destroy what exists ────────────────────────────────────


@pytest.mark.asyncio
async def test_a_failed_summary_keeps_the_previous_one(db_client):
    """One provider hiccup must not cost the team its only shared view of
    progress — and an empty summary reads as "no progress", not "unknown"."""
    await _seed_room(db_client, messages=TeamSummaryWorker.MESSAGE_THRESHOLD)
    repo = TeamBulletinRepository(db_client)
    await repo.upsert_summary(TEAM, "known good progress")

    w = _worker(db_client, summary=RuntimeError("provider exploded"))
    await w.run_once()

    assert (await repo.get_summary(TEAM)).content == "known good progress"


@pytest.mark.asyncio
async def test_a_failure_does_not_raise_out_of_the_pass(db_client):
    """Iron rule #14: this is opportunistic work. It may not take anything with
    it when it fails."""
    await _seed_room(db_client, messages=TeamSummaryWorker.MESSAGE_THRESHOLD)
    w = _worker(db_client, summary=RuntimeError("provider exploded"))

    await w.run_once()  # must not raise


@pytest.mark.asyncio
async def test_an_empty_model_reply_is_not_written(db_client):
    """A model that returns whitespace has told us nothing; writing it would
    replace a real summary with a blank one."""
    await _seed_room(db_client, messages=TeamSummaryWorker.MESSAGE_THRESHOLD)
    repo = TeamBulletinRepository(db_client)
    await repo.upsert_summary(TEAM, "still true")

    w = _worker(db_client, summary="   ")
    await w.run_once()

    assert (await repo.get_summary(TEAM)).content == "still true"


@pytest.mark.asyncio
async def test_one_bad_team_does_not_block_the_others(db_client):
    """Per-team isolation, the same shape as the memory worker's per-scope
    isolation: a single unsummarisable room must not stall every other room."""
    await _seed_room(db_client, messages=TeamSummaryWorker.MESSAGE_THRESHOLD)
    await db_client.insert("teams", {"team_id": "team_2", "owner_user_id": OWNER, "name": "T2"})
    await db_client.insert("team_members", {"team_id": "team_2", "agent_id": "agent_b"})
    await db_client.insert(
        "bus_channels",
        {
            "channel_id": "ch_team_2",
            "channel_type": "group",
            "created_by": "team_team_2",
            "name": "T2",
        },
    )
    for i in range(TeamSummaryWorker.MESSAGE_THRESHOLD):
        await db_client.insert(
            "bus_messages",
            {
                "message_id": f"t2m{i}",
                "channel_id": "ch_team_2",
                "from_agent": "agent_a",
                "content": "other room",
                "msg_type": "text",
                "created_at": _ts(i),
            },
        )

    w = TeamSummaryWorker(db_client)
    seen = []

    async def flaky(*, team_id, transcript, bearer=""):
        seen.append(team_id)
        if team_id == TEAM:
            raise RuntimeError("this one is cursed")
        return "team 2 is fine"

    w._summarise = flaky
    await w.run_once()

    assert set(seen) == {TEAM, "team_2"}
    repo = TeamBulletinRepository(db_client)
    assert (await repo.get_summary("team_2")).content == "team 2 is fine"


# ── budget ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_over_long_summary_is_truncated_not_refused(db_client):
    """The opposite policy from user entries, and deliberately so. A user must
    never have a rule silently shortened, because they would go on believing
    the whole rule applies. Nobody is relying on the exact wording of a
    generated paragraph, and refusing it outright would leave the team with no
    progress view at all — so here a cap is kinder than a rejection.
    """
    from xyz_agent_context.schema.team_schema import BULLETIN_MAX_SUMMARY_CHARS

    await _seed_room(db_client, messages=TeamSummaryWorker.MESSAGE_THRESHOLD)
    w = _worker(db_client, summary="w" * (BULLETIN_MAX_SUMMARY_CHARS * 3))

    await w.run_once()

    stored = (await TeamBulletinRepository(db_client).get_summary(TEAM)).content
    assert len(stored) <= BULLETIN_MAX_SUMMARY_CHARS


@pytest.mark.asyncio
async def test_the_summary_never_eats_the_user_entry_budget(db_client):
    from xyz_agent_context.schema.team_schema import BULLETIN_MAX_ENTRIES

    await _seed_room(db_client, messages=TeamSummaryWorker.MESSAGE_THRESHOLD)
    repo = TeamBulletinRepository(db_client)
    for i in range(BULLETIN_MAX_ENTRIES):
        await repo.add(team_id=TEAM, content=f"rule {i}", source="user", author_id=OWNER)

    w = _worker(db_client)
    await w.run_once()

    # The summary landed even with the entry budget completely full.
    assert await repo.get_summary(TEAM) is not None
    assert (await repo.usage(TEAM)).entry_count == BULLETIN_MAX_ENTRIES


# ── what the model is shown ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_model_sees_the_rooms_messages(db_client):
    """A summary of nothing is a hallucination waiting to happen."""
    await _seed_room(db_client, messages=TeamSummaryWorker.MESSAGE_THRESHOLD)
    w = _worker(db_client)

    await w.run_once()

    assert "working on step 0" in w.calls[0]["transcript"]


# ── it is actually running ───────────────────────────────────────────────────


def test_the_worker_is_started_and_stopped_by_the_app():
    """A worker nobody starts is dead code that passes its own tests; one
    nobody stops logs a connection error on every clean shutdown, because its
    poll loop outlives the db client it holds."""
    import pathlib

    main = pathlib.Path(__file__).resolve().parents[2] / "backend" / "main.py"
    src = main.read_text()

    assert "TeamSummaryWorker(db)" in src
    assert "team_summary_worker.start()" in src
    assert "await summary_worker.stop()" in src
    # Order matters: stop the loop before the client it uses goes away.
    assert src.index("await summary_worker.stop()") < src.index("await close_db_client()")


# ── the production path, NOT stubbed ────────────────────────────────────────
#
# Every test above replaces `_summarise` wholesale, and the docstring on that
# method presented it as a virtue ("the rules around this call matter more than
# the call itself"). That reasoning was wrong twice over, and review caught both:
# the method called `set_cost_context` with keyword arguments the function does
# not have (TypeError on every real run), and it never injected the owner's
# credentials, so on cloud every call would fall through to the platform key and
# 401 — the 2026-07 incident that cost long-term memory two weeks.
#
# Both are invisible to a stubbed `_summarise`. These tests exercise the real
# one with only the SDK faked out, which is the smallest seam that still runs
# the argument assembly.


@pytest.mark.asyncio
async def test_the_real_summarise_assembles_a_valid_cost_context(db_client, monkeypatch):
    """`set_cost_context(agent_id, db)` — two positional params, no user_id, no
    label. Calling it wrong raises TypeError, which `run_once` swallows into a
    warning, so the worker looks alive while never writing a single summary."""
    from xyz_agent_context.services import team_summary_worker as mod

    seen = {}

    def fake_set(*args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs

    monkeypatch.setattr(mod, "set_cost_context", fake_set)
    monkeypatch.setattr(mod, "clear_cost_context", lambda: None)
    monkeypatch.setattr(mod, "_inject_team_credentials", _noop_creds)
    monkeypatch.setattr(mod, "get_helper_sdk", lambda: _FakeSdk("a summary"))

    await _seed_room(db_client, messages=1)
    await db_client.insert("agents", {"agent_id": "agent_a", "agent_name": "A", "created_by": OWNER})
    out = await TeamSummaryWorker(db_client)._summarise(
        team_id=TEAM, transcript="x: y", bearer="agent_a"
    )

    assert out == "a summary"

    # Bind the captured call against the REAL signature. Asserting a shape I
    # happened to expect is what let the previous version through: the fake
    # accepted anything, so it certified my guess rather than the contract.
    import inspect

    from xyz_agent_context.utils import cost_tracker

    inspect.signature(cost_tracker.set_cost_context).bind(*seen["args"], **seen["kwargs"])

    passed = list(seen["args"]) + list(seen["kwargs"].values())
    assert db_client in passed

    # And a NON-EMPTY agent id. Every helper SDK drops the record when it is
    # empty — before the warn-on-missing-usage call — so an empty id is not
    # "attributed with a blank"; it is silently unrecorded, with the docstring
    # still claiming otherwise.
    bearer = [v for v in passed if isinstance(v, str)]
    assert bearer and bearer[0], "cost context carries an empty agent_id"


@pytest.mark.asyncio
async def test_the_real_summarise_injects_the_teams_credentials(db_client, monkeypatch):
    """A detached background task inherits no per-request ContextVars, so
    without this every cloud call uses the platform key and 401s."""
    from xyz_agent_context.services import team_summary_worker as mod

    injected = []

    async def fake_inject(team_id, db):
        injected.append(team_id)

    monkeypatch.setattr(mod, "_inject_team_credentials", fake_inject)
    monkeypatch.setattr(mod, "set_cost_context", lambda *a, **k: None)
    monkeypatch.setattr(mod, "clear_cost_context", lambda: None)
    monkeypatch.setattr(mod, "get_helper_sdk", lambda: _FakeSdk("s"))

    await _seed_room(db_client, messages=1)
    await TeamSummaryWorker(db_client)._summarise(
        team_id=TEAM, transcript="x: y", bearer="agent_a"
    )

    assert injected == [TEAM], "the team's owner credentials were never resolved"


@pytest.mark.asyncio
async def test_credentials_are_cleared_before_they_are_resolved(db_client, monkeypatch):
    """run_once walks tenants in sequence in ONE task. Without a reset first, a
    team whose owner cannot be resolved inherits the previous team's
    credentials — a cross-tenant leak, not merely a stale config."""
    from xyz_agent_context.agent_framework.providers import resolver
    from xyz_agent_context.services.team_summary_worker import _inject_team_credentials

    order = []
    monkeypatch.setattr(resolver, "clear_user_config", lambda: order.append("clear"))

    async def fake_resolve(user_id, db, agent_id=None):
        order.append(f"resolve:{user_id}")

    monkeypatch.setattr(resolver, "resolve_and_set_provider_for_user", fake_resolve)

    await db_client.insert("teams", {"team_id": TEAM, "owner_user_id": OWNER, "name": "T"})
    await _inject_team_credentials(TEAM, db_client)

    assert order == ["clear", f"resolve:{OWNER}"]


@pytest.mark.asyncio
async def test_an_unresolvable_team_leaves_credentials_cleared(db_client, monkeypatch):
    """The leak case made concrete: no owner row means we must NOT fall through
    holding whatever the last team put there."""
    from xyz_agent_context.agent_framework.providers import resolver
    from xyz_agent_context.services.team_summary_worker import _inject_team_credentials

    order = []
    monkeypatch.setattr(resolver, "clear_user_config", lambda: order.append("clear"))

    async def fake_resolve(user_id, db, agent_id=None):
        order.append("resolve")

    monkeypatch.setattr(resolver, "resolve_and_set_provider_for_user", fake_resolve)

    await _inject_team_credentials("team_that_does_not_exist", db_client)

    assert order == ["clear"], "resolution ran, or the reset did not"


class _Result:
    def __init__(self, text):
        self.final_output = text


class _FakeSdk:
    def __init__(self, text):
        self._text = text

    async def llm_function(self, **kwargs):
        return _Result(self._text)


async def _noop_creds(team_id, db):
    return None


# ── the platform must not feed its own bookkeeping back to itself ───────────


@pytest.mark.asyncio
async def test_system_lines_do_not_count_toward_the_threshold(db_client):
    """The bulletin notice THIS FEATURE writes lands in the same room. Counting
    it means a quiet team can be pushed over the threshold by the announcement
    of its own last summary — the platform triggering itself."""
    await _seed_room(db_client, messages=0)
    for i in range(TeamSummaryWorker.MESSAGE_THRESHOLD * 2):
        await db_client.insert("bus_messages", {
            "message_id": f"sys{i}", "channel_id": CHANNEL, "from_agent": "usr_1",
            "content": "Team bulletin updated.", "msg_type": "system_bulletin",
            "created_at": _ts(i),
        })

    w = _worker(db_client)
    await w.run_once()

    assert w.calls == [], "system notices were counted as team activity"


@pytest.mark.asyncio
async def test_system_lines_are_not_shown_to_the_summariser(db_client):
    """Feeding "Team bulletin updated." in invites the model to report the
    platform's own bookkeeping as team progress."""
    await _seed_room(db_client, messages=TeamSummaryWorker.MESSAGE_THRESHOLD)
    await db_client.insert("bus_messages", {
        "message_id": "sysx", "channel_id": CHANNEL, "from_agent": "usr_1",
        "content": "Team bulletin updated.", "msg_type": "system_bulletin",
        "created_at": _ts(900),
    })

    w = _worker(db_client)
    await w.run_once()

    assert "Team bulletin updated." not in w.calls[0]["transcript"]


# ── L2: silence must not be ambiguous ───────────────────────────────────────


@pytest.mark.asyncio
async def test_a_pass_reports_what_it_did(db_client):
    """With only per-failure warnings, "every room is quiet" and "every room is
    failing" are the same observation. This is the signal that would have
    surfaced the two production-only faults review had to find by reading."""
    await _seed_room(db_client, messages=TeamSummaryWorker.MESSAGE_THRESHOLD)
    w = _worker(db_client)

    await w.run_once()
    assert w.last_pass == {"rooms": 1, "summarised": 1, "failed": 0}


@pytest.mark.asyncio
async def test_a_failing_pass_is_distinguishable_from_a_quiet_one(db_client):
    await _seed_room(db_client, messages=TeamSummaryWorker.MESSAGE_THRESHOLD)
    w = _worker(db_client, summary=RuntimeError("boom"))

    await w.run_once()
    assert w.last_pass["failed"] == 1
    assert w.last_pass["summarised"] == 0


# ── the cost bearer ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_lead_agent_bears_the_cost(db_client):
    await _seed_room(db_client, messages=1)
    await db_client.update("teams", {"team_id": TEAM}, {"lead_agent_id": "agent_lead"})
    await db_client.insert("team_members", {"team_id": TEAM, "agent_id": "agent_lead"})

    assert await TeamSummaryWorker(db_client)._cost_bearer(TEAM) == "agent_lead"


@pytest.mark.asyncio
async def test_without_a_lead_the_earliest_member_bears_it(db_client):
    """Same rule the room already uses for "who answers with no @mention", so
    the cost lands on the member the team already treats as its default."""
    await _seed_room(db_client, messages=1)  # seeds agent_a as the first member
    await db_client.insert("team_members", {"team_id": TEAM, "agent_id": "agent_z"})

    assert await TeamSummaryWorker(db_client)._cost_bearer(TEAM) == "agent_a"


@pytest.mark.asyncio
async def test_a_team_with_no_members_has_no_bearer(db_client):
    await db_client.insert("teams", {"team_id": "team_empty", "owner_user_id": OWNER, "name": "E"})
    assert await TeamSummaryWorker(db_client)._cost_bearer("team_empty") == ""


@pytest.mark.asyncio
async def test_patrol_lines_do_not_count_toward_the_threshold(db_client):
    """#259's patrol messages are the platform chasing stalled work, not team
    activity. Patrol speaks precisely in rooms where nothing is moving (rate cap
    6 per 30 min), so counting it lets a room with NO real work reach the
    threshold on the platform's own chase messages inside a couple of hours."""
    from xyz_agent_context.message_bus.patrol import PATROL_MSG_TYPE

    await _seed_room(db_client, messages=0)
    for i in range(TeamSummaryWorker.MESSAGE_THRESHOLD * 2):
        await db_client.insert("bus_messages", {
            "message_id": f"pat{i}", "channel_id": CHANNEL,
            "from_agent": f"team_{TEAM}", "content": "where does this stand?",
            "msg_type": PATROL_MSG_TYPE, "created_at": _ts(i),
        })

    w = _worker(db_client)
    await w.run_once()

    assert w.calls == [], "patrol chase messages were counted as team activity"


@pytest.mark.asyncio
async def test_patrol_lines_are_not_shown_to_the_summariser(db_client):
    """Their from_agent is a synthetic `team_<id>` marker that never resolves
    through member_map, so they would also read as a member speaking."""
    from xyz_agent_context.message_bus.patrol import PATROL_MSG_TYPE

    await _seed_room(db_client, messages=TeamSummaryWorker.MESSAGE_THRESHOLD)
    await db_client.insert("bus_messages", {
        "message_id": "pat", "channel_id": CHANNEL, "from_agent": f"team_{TEAM}",
        "content": "chasing the parser task", "msg_type": PATROL_MSG_TYPE,
        "created_at": _ts(950),
    })

    w = _worker(db_client)
    await w.run_once()

    assert "chasing the parser task" not in w.calls[0]["transcript"]


def test_the_filter_is_built_from_constants_not_retyped_strings():
    """The first version hard-coded two literals and #259's third type slipped
    past it. Importing the constants is what makes the next one impossible to
    miss silently.

    Asserted as "every registered platform type is in the filter", not as a
    fixed list: pinning the exact set meant this test failed the moment two more
    types were registered (system_cascade / system_roster), which is a green-to-
    red signal about the wrong thing. What must hold is that the worker filters
    ALL of them — a new type nobody added here would let the platform trigger
    itself again, which is the actual fault this guards.
    """
    from xyz_agent_context.message_bus.system_messages import PLATFORM_MSG_TYPES
    from xyz_agent_context.services.team_summary_worker import _SYSTEM_MSG_TYPES

    assert set(_SYSTEM_MSG_TYPES) == set(PLATFORM_MSG_TYPES)
    # And the registry is not empty, or the assertion above passes vacuously.
    assert len(PLATFORM_MSG_TYPES) >= 3


@pytest.mark.asyncio
async def test_a_team_with_no_members_is_not_summarised_at_all(db_client):
    """The empty-bearer path is now closed rather than merely unlikely.

    Every helper SDK discards a cost record whose agent id is empty, so
    summarising a memberless team would burn the owner's tokens with nothing
    written down anywhere. The docstring used to assert this case "has nothing
    to summarise either" — an assertion about the world that the code did not
    enforce, and the kind that stops being true the moment someone removes the
    last member from a busy room.
    """
    await db_client.insert("teams", {"team_id": "team_empty", "owner_user_id": OWNER, "name": "E"})
    await db_client.insert("bus_channels", {
        "channel_id": "ch_empty", "channel_type": "group",
        "created_by": "team_team_empty", "name": "E",
    })
    for i in range(TeamSummaryWorker.MESSAGE_THRESHOLD * 2):
        await db_client.insert("bus_messages", {
            "message_id": f"e{i}", "channel_id": "ch_empty", "from_agent": "a",
            "content": "busy room, nobody home", "msg_type": "text",
            "created_at": _ts(i),
        })

    w = _worker(db_client)
    await w.run_once()

    assert w.calls == [], "summarised a team with no cost bearer"
    assert w.last_pass["failed"] == 0, "skipping is not a failure"


@pytest.mark.asyncio
async def test_the_bearer_rule_is_the_rooms_own_default_responder(db_client):
    """One rule, one implementation. This was a second hand-written copy of
    `resolve_default_responder` plus its own raw team_members query."""
    from xyz_agent_context.schema.team_schema import resolve_default_responder

    await _seed_room(db_client, messages=1)
    await db_client.update("teams", {"team_id": TEAM}, {"lead_agent_id": "agent_lead"})
    await db_client.insert("team_members", {"team_id": TEAM, "agent_id": "agent_lead"})

    bearer = await TeamSummaryWorker(db_client)._cost_bearer(TEAM)
    team = await db_client.get_one("teams", {"team_id": TEAM})
    assert bearer == resolve_default_responder(
        team["lead_agent_id"], ["agent_a", "agent_lead"]
    )


@pytest.mark.asyncio
async def test_the_health_endpoint_exposes_the_last_pass():
    """Counters nothing reads are counters that do not exist. The blind spot
    they close — "quiet" versus "all failing" both looking like a worker that is
    simply up — stays open if they never leave the process.

    Reported, not judged: one team with a bad provider key must not fail the
    container's probe, so `status` does not depend on `failed`.
    """
    import backend.main as main

    class _W:
        running = True
        last_pass = {"rooms": 3, "summarised": 1, "failed": 2}

    main.app.state.team_summary_worker = _W()
    try:
        body = await main.health()
    finally:
        del main.app.state.team_summary_worker

    assert body["team_summary"] == {
        "running": True, "rooms": 3, "summarised": 1, "failed": 2,
    }
    # Reported, never judged: two failing teams must not fail the container.
    assert body["status"] == "healthy"
