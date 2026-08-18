"""
@file_name: test_team_posting_cap.py
@date: 2026-08-17
@description: The hop cap has to RUN on the path the tool actually takes.

`team_cascade_depth` was lifted out of `MessageBusTrigger`, where `self._bus._db`
is the RAW backend and SQL goes through verbatim. Every caller of the extracted
function holds an `AsyncDatabaseClient` instead, which has no `.placeholder` — so
reading one raised inside the cap check and took the whole send with it: every
`message_team` call returned `{"success": false}` while the room stayed silent.

It surfaced only as a log line during an unrelated test, which is why this file
exists: the cap is a loop-breaker, and a loop-breaker that raises is worse than
one that is absent, because the send fails too.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.message_bus.local_bus import LocalMessageBus
from xyz_agent_context.message_bus.team_posting import (
    MAX_TEAM_AGENT_HOPS,
    post_team_reply,
    team_cascade_depth,
)
from xyz_agent_context.schema.team_schema import (
    TEAM_ROOM_OWNER_PREFIX,
    USER_SENDER_PREFIX,
)

TEAM, CHANNEL = "t_cap", "ch_cap"
A, B, USER = "agent_a", "agent_b", f"{USER_SENDER_PREFIX}u1"


async def _room(db):
    await db.insert("bus_channels", {
        "channel_id": CHANNEL, "name": "cap", "channel_type": "group",
        "created_by": f"{TEAM_ROOM_OWNER_PREFIX}{TEAM}",
    })
    for aid, nm in ((A, "Ana"), (B, "Bo")):
        await db.insert("bus_channel_members", {"channel_id": CHANNEL, "agent_id": aid})
        await db.insert("agents", {"agent_id": aid, "agent_name": nm, "created_by": "u1"})
    await db.insert("teams", {"team_id": TEAM, "owner_user_id": "u1", "name": "Cap"})


@pytest.mark.asyncio
async def test_the_cap_query_runs_against_the_client(db_client):
    """The regression itself: reading `.placeholder` off the client raised."""
    await _room(db_client)
    assert await team_cascade_depth(db_client, CHANNEL) == 0


@pytest.mark.asyncio
async def test_a_reply_lands_and_resolves_its_mentions(db_client):
    await _room(db_client)
    bus = LocalMessageBus(backend=db_client._backend)
    roster = [{"agent_id": A, "name": "Ana"}, {"agent_id": B, "name": "Bo"}]

    out = await post_team_reply(
        db=db_client, bus=bus, agent_id=A, team_id=TEAM,
        channel_id=CHANNEL, text="@Bo over to you", roster=roster,
    )

    assert out["message_id"]
    assert out["mentioned"] == [B]
    assert out["capped"] == {"names": [], "everyone": False}


@pytest.mark.asyncio
async def test_the_cap_fires_and_names_who_was_not_reached(db_client):
    """A human word resets the count, so the pile has to be agent-only."""
    await _room(db_client)
    bus = LocalMessageBus(backend=db_client._backend)
    roster = [{"agent_id": A, "name": "Ana"}, {"agent_id": B, "name": "Bo"}]

    for i in range(MAX_TEAM_AGENT_HOPS):
        await bus.send_message(from_agent=A, to_channel=CHANNEL, content=f"hop {i}")

    out = await post_team_reply(
        db=db_client, bus=bus, agent_id=A, team_id=TEAM,
        channel_id=CHANNEL, text="@Bo still there?", roster=roster,
    )

    assert out["mentioned"] == []
    assert out["capped"]["names"] == ["Bo"]


@pytest.mark.asyncio
async def test_a_human_word_resets_the_count(db_client):
    await _room(db_client)
    bus = LocalMessageBus(backend=db_client._backend)
    for i in range(MAX_TEAM_AGENT_HOPS):
        await bus.send_message(from_agent=A, to_channel=CHANNEL, content=f"hop {i}")
    await bus.send_message(from_agent=USER, to_channel=CHANNEL, content="carry on")

    assert await team_cascade_depth(db_client, CHANNEL) == 0
