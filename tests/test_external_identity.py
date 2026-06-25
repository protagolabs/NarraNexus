"""
@file_name: test_external_identity.py
@author: NetMind.AI
@date: 2026-06-24
@description: IM identity-tenant — external_subject_id derives a stable per-room
scope identity for external IM conversations.

Room-derived: a DM room (1:1) yields a per-person scope; a group room yields a
per-group (community) scope. The room_id IS the discriminator, so one rule covers
both. The "ext:" prefix lets the executor/broker recognise external subjects.
"""
import json

import pytest

from xyz_agent_context.channel.external_identity import (
    EXTERNAL_USER_TYPE,
    ensure_external_user,
    external_subject_id,
)


def test_basic_shape():
    sid = external_subject_id("slack", "room1")
    assert sid.startswith("ext:slack:")
    # room is hashed to 16 hex chars
    suffix = sid.rsplit(":", 1)[1]
    assert len(suffix) == 16
    assert all(c in "0123456789abcdef" for c in suffix)


def test_fits_user_id_column_even_for_long_room_id():
    # users.user_id is VARCHAR(64); a long Matrix-style room id must still fit.
    long_room = "!" + "a" * 200 + ":matrix.very.long.homeserver.example.com"
    assert len(external_subject_id("narramessenger", long_room)) <= 64


def test_distinct_per_room():
    # DM rooms and group rooms are just distinct room_ids → distinct scopes.
    assert external_subject_id("slack", "dm_alice") != external_subject_id("slack", "group_x")


def test_stable_for_same_room():
    assert external_subject_id("nm", "!r:srv") == external_subject_id("nm", "!r:srv")


def test_distinct_per_channel():
    assert external_subject_id("slack", "room1") != external_subject_id("lark", "room1")


def test_is_recognisable_as_external():
    assert external_subject_id("slack", "room1").startswith("ext:")


def test_rejects_empty():
    with pytest.raises(ValueError):
        external_subject_id("", "room1")
    with pytest.raises(ValueError):
        external_subject_id("slack", "")


# ---- provisioning ---------------------------------------------------------

@pytest.mark.asyncio
async def test_ensure_external_user_creates_row(db_client):
    sid = external_subject_id("slack", "room1")
    await ensure_external_user(
        db_client, subject_id=sid, channel="slack", room_id="room1",
        display_name="Alice", owner_user_id="owner_x",
    )
    row = await db_client.get_one("users", {"user_id": sid})
    assert row is not None
    assert row["user_type"] == EXTERNAL_USER_TYPE
    assert row["display_name"] == "Alice"
    meta = json.loads(row["metadata"])
    assert meta == {"channel": "slack", "room_id": "room1", "owner_user_id": "owner_x"}


@pytest.mark.asyncio
async def test_ensure_external_user_is_idempotent(db_client):
    sid = external_subject_id("slack", "room1")
    for _ in range(3):
        await ensure_external_user(
            db_client, subject_id=sid, channel="slack", room_id="room1",
        )
    rows = await db_client.get("users", {"user_id": sid})
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_ensure_external_user_noop_without_db():
    # Best-effort: must not raise when db is unavailable.
    await ensure_external_user(None, subject_id="ext:slack:x", channel="slack", room_id="x")
