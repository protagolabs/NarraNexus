"""
@file_name: test_im_short_term_repository.py
@author: NetMind.AI
@date: 2026-06-24
@description: T3 — IMShortTermRepository: lightweight cross-turn memory for
distrust IM-channel visitors, keyed by (agent_id, im_room_id).

Covers: append + recent (chronological), per-room isolation (the DM-isolation /
group-sharing property is purely the im_room_id key), recent limit, and bounded
retention via cleanup_older_than_days.
"""
from datetime import datetime, timedelta, timezone

import pytest

from xyz_agent_context.repository.im_short_term_repository import IMShortTermRepository


async def _append(repo, room, role, body, sender="s1"):
    await repo.append(
        agent_id="agent_x", owner_id="owner_x", channel="narramessenger",
        im_room_id=room, sender=sender, role=role, body=body,
    )


@pytest.mark.asyncio
async def test_append_and_recent_chronological(db_client):
    repo = IMShortTermRepository(db_client)
    await _append(repo, "room1", "user", "hello")
    await _append(repo, "room1", "agent", "hi there")

    rows = await repo.recent("agent_x", "room1", limit=10)
    assert [r["body"] for r in rows] == ["hello", "hi there"]
    assert [r["role"] for r in rows] == ["user", "agent"]


@pytest.mark.asyncio
async def test_recent_isolates_by_room(db_client):
    """A DM room is a distinct im_room_id → another room's history never leaks."""
    repo = IMShortTermRepository(db_client)
    await _append(repo, "roomA", "user", "secretA")
    await _append(repo, "roomB", "user", "secretB")

    a = await repo.recent("agent_x", "roomA", limit=10)
    assert all(r["im_room_id"] == "roomA" for r in a)
    assert "secretB" not in [r["body"] for r in a]


@pytest.mark.asyncio
async def test_recent_limit_returns_latest(db_client):
    repo = IMShortTermRepository(db_client)
    for i in range(5):
        await _append(repo, "room1", "user", f"m{i}")

    rows = await repo.recent("agent_x", "room1", limit=2)
    assert [r["body"] for r in rows] == ["m3", "m4"]  # latest 2, chronological


@pytest.mark.asyncio
async def test_recent_scoped_by_agent(db_client):
    repo = IMShortTermRepository(db_client)
    await repo.append(agent_id="agent_x", owner_id="o", channel="nm",
                      im_room_id="room1", sender="s", role="user", body="for_x")
    await repo.append(agent_id="agent_y", owner_id="o", channel="nm",
                      im_room_id="room1", sender="s", role="user", body="for_y")

    rows = await repo.recent("agent_x", "room1", limit=10)
    assert [r["body"] for r in rows] == ["for_x"]


@pytest.mark.asyncio
async def test_cleanup_older_than_days(db_client):
    repo = IMShortTermRepository(db_client)
    # An old row inserted directly with a stale created_at.
    stale = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat(sep=" ")
    await db_client.insert("instance_im_short_term", {
        "agent_id": "agent_x", "owner_id": "o", "channel": "nm",
        "im_room_id": "room1", "sender": "s", "role": "user",
        "body": "old", "created_at": stale,
    })
    await _append(repo, "room1", "user", "fresh")

    deleted = await repo.cleanup_older_than_days(1)
    assert deleted == 1
    rows = await repo.recent("agent_x", "room1", limit=10)
    assert [r["body"] for r in rows] == ["fresh"]
