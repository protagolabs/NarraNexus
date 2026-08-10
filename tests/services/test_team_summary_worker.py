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

    async def fake_llm(*, team_id, transcript):
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

    async def flaky(*, team_id, transcript):
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
