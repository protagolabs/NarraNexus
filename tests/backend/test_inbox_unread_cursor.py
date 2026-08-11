"""
@file_name: test_inbox_unread_cursor.py
@author:
@date: 2026-08-11
@description: Which cursor the inbox counts against — the one the user can move.

`bus_channel_members` carries two cursors. `last_processed_at` is the trigger's
bookmark ("I drove this agent past here"); `last_read_at` is the one the unread
list in every turn's context is measured against, and the one the inbox's
"mark room read" button writes.

The inbox counted against `last_processed_at or last_read_at`. Since the trigger
advances `last_processed_at` on every poll, the count was permanently 0 — and
the frontend only fires the mark-read request when the count is above zero. So
the single control that could reset `last_read_at` was unreachable, for exactly
the rooms whose backlog was growing.

Pinned here:
  * a room whose trigger cursor is current but whose read cursor is not still
    reports its backlog
  * the mark-read endpoint clears it — i.e. the escape hatch is reachable
  * own posts are still excluded from the count
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.routes.inbox as inbox_mod
from xyz_agent_context.utils.timezone import utc_now


AGENT = "agent_me"
PEER = "agent_peer"
ROOM = "ch_room"
USER = "usr_1"


@pytest.fixture
def client(db_client, monkeypatch):
    """The real inbox router over the real sqlite fixture.

    The sibling suite mocks the DB to assert SQL shape; this one needs the
    actual cursor arithmetic, so it runs the query for real.

    Through `monkeypatch`, not a bare assignment: the override closes over a
    fixture-scoped client that is shut down at teardown, so leaving it installed
    would hand every later test touching this router a dead connection — with a
    failure pointing nowhere near the cause.
    """
    app = FastAPI()
    app.include_router(inbox_mod.router, prefix="/api/agent-inbox")

    async def _get_db_override():
        return db_client

    monkeypatch.setattr(inbox_mod, "_get_db", _get_db_override)
    return TestClient(app)


def _now() -> str:
    return utc_now().isoformat()


async def _seed(db, *, processed_at=None, read_at=None):
    await db.insert("agents", {
        "agent_id": AGENT, "agent_name": "Mia", "created_by": USER,
    })
    await db.insert("bus_channels", {
        "channel_id": ROOM, "name": "room", "channel_type": "group",
        "created_by": "team_t1",
    })
    row = {"channel_id": ROOM, "agent_id": AGENT}
    if processed_at:
        row["last_processed_at"] = processed_at
    if read_at:
        row["last_read_at"] = read_at
    await db.insert("bus_channel_members", row)
    await db.insert("bus_channel_members", {"channel_id": ROOM, "agent_id": PEER})
    # Timestamps must sit in the PAST relative to utc_now(): the mark-read
    # endpoint advances the cursor to "now", so a message dated in the future
    # would survive it and the test would be measuring its own fixture.
    base = utc_now() - timedelta(hours=2)
    for i, (sender, text) in enumerate(
        [(PEER, "did you see this"), (AGENT, "my own post"), (PEER, "and this")]
    ):
        await db.insert("bus_messages", {
            "message_id": f"m{i}", "channel_id": ROOM, "from_agent": sender,
            "content": text, "created_at": (base + timedelta(minutes=i)).isoformat(),
        })


@pytest.mark.asyncio
async def test_a_current_trigger_cursor_does_not_hide_the_backlog(db_client, client):
    """The exact production shape: the trigger has been through this room many
    times, so `last_processed_at` is fresh, while `last_read_at` never moved."""
    await _seed(db_client, processed_at=_now(), read_at=None)

    r = client.get(f"/api/agent-inbox?agent_id={AGENT}")

    assert r.status_code == 200
    rooms = r.json()["rooms"]
    assert len(rooms) == 1
    # Two peer messages unread; the agent's own post is not its own backlog.
    assert rooms[0]["unread_count"] == 2


@pytest.mark.asyncio
async def test_the_read_cursor_is_what_clears_the_count(db_client, client):
    await _seed(db_client, processed_at=None, read_at=_now())

    r = client.get(f"/api/agent-inbox?agent_id={AGENT}")

    assert r.json()["rooms"][0]["unread_count"] == 0


@pytest.mark.asyncio
async def test_the_escape_hatch_is_reachable(db_client, client):
    """The whole point: the user can see a backlog, so the user can clear it."""
    await _seed(db_client, processed_at=_now(), read_at=None)
    assert client.get(f"/api/agent-inbox?agent_id={AGENT}").json()[
        "rooms"
    ][0]["unread_count"] == 2

    marked = client.post(f"/api/agent-inbox/rooms/{ROOM}/read?agent_id={AGENT}")

    assert marked.status_code == 200
    assert client.get(f"/api/agent-inbox?agent_id={AGENT}").json()[
        "rooms"
    ][0]["unread_count"] == 0
