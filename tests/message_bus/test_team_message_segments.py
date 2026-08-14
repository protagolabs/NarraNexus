"""
@file_name: test_team_message_segments.py
@author: NarraNexus
@date: 2026-08-12
@description: Carrying the monologue/reply boundary from the run to the wall.

`run_collector` preserves the boundary; this is the rest of the path — bus
write, storage, read back — so the room can lay deliberation out differently
from an answer.

Two properties are load-bearing and pinned here:

  * `content` does not change. It is what every TEXT consumer reads: the memory
    index, and other agents' scrollback. A rendering feature must not rewrite
    the thing the rest of the system reads.
  * Absent segments are normal, not an error. Every message written before this
    change has none, and per iron rule #2 there is no backfill and no
    compatibility shim — the reader renders those as one block, which is exactly
    today's behaviour.
"""

from __future__ import annotations

import json

import pytest

from xyz_agent_context.message_bus.local_bus import LocalMessageBus

CHANNEL = "ch_1"


@pytest.fixture
async def bus(db_client):
    await db_client.insert("bus_channels", {
        "channel_id": CHANNEL, "channel_type": "group",
        "created_by": "team_t1", "name": "T",
    })
    return LocalMessageBus(backend=db_client._backend)


async def _row(db, message_id):
    return await db.get_one("bus_messages", {"message_id": message_id})


@pytest.mark.asyncio
async def test_segments_are_stored_with_the_message(bus, db_client):
    mid = await bus.send_message(
        from_agent="agent_a",
        to_channel=CHANNEL,
        content="thinkinganswering",
        segments=[
            {"kind": "monologue", "text": "thinking"},
            {"kind": "reply", "text": "answering"},
        ],
    )

    row = await _row(db_client, mid)
    assert json.loads(row["segments"]) == [
        {"kind": "monologue", "text": "thinking"},
        {"kind": "reply", "text": "answering"},
    ]


@pytest.mark.asyncio
async def test_content_is_untouched_by_segmenting(bus, db_client):
    """The text every other consumer reads must be byte-identical."""
    mid = await bus.send_message(
        from_agent="agent_a",
        to_channel=CHANNEL,
        content="thinkinganswering",
        segments=[{"kind": "reply", "text": "thinkinganswering"}],
    )

    row = await _row(db_client, mid)
    assert row["content"] == "thinkinganswering"


@pytest.mark.asyncio
async def test_a_message_without_segments_stores_null(bus, db_client):
    """Not an empty list: NULL is "this message predates segments or came from a
    path that has none", and the reader must be able to tell that from "this
    turn produced no segments"."""
    mid = await bus.send_message(
        from_agent="agent_a", to_channel=CHANNEL, content="plain"
    )

    row = await _row(db_client, mid)
    assert row.get("segments") in (None, "")


@pytest.mark.asyncio
async def test_an_empty_segment_list_is_stored_as_null_too(bus, db_client):
    """A silent turn is dropped upstream; if one ever reached here, an empty
    list carries no information a reader could use, so it is not worth a row
    value that looks like data."""
    mid = await bus.send_message(
        from_agent="agent_a", to_channel=CHANNEL, content="plain", segments=[]
    )

    row = await _row(db_client, mid)
    assert row.get("segments") in (None, "")


@pytest.mark.asyncio
async def test_reading_a_message_back_returns_parsed_segments(bus, db_client):
    """Stored as JSON text, handed back as a list — no consumer should have to
    know it was serialised."""
    await bus.send_message(
        from_agent="agent_a",
        to_channel=CHANNEL,
        content="ab",
        segments=[{"kind": "monologue", "text": "a"}, {"kind": "reply", "text": "b"}],
    )

    msgs = await bus.get_messages(CHANNEL, limit=10)
    assert msgs[-1].segments == [
        {"kind": "monologue", "text": "a"},
        {"kind": "reply", "text": "b"},
    ]


@pytest.mark.asyncio
async def test_a_legacy_message_reads_back_as_no_segments(bus, db_client):
    """Every message written before this change. No backfill, no shim: the
    reader gets None and renders one block, which is today's behaviour."""
    await bus.send_message(from_agent="agent_a", to_channel=CHANNEL, content="old")

    msgs = await bus.get_messages(CHANNEL, limit=10)
    assert msgs[-1].segments is None


@pytest.mark.asyncio
async def test_corrupt_segments_do_not_break_the_read(bus, db_client):
    """A hand-edited or half-written row must not take the whole room's
    transcript down — losing the layout of one message is a degradation, losing
    the room is an outage."""
    mid = await bus.send_message(from_agent="agent_a", to_channel=CHANNEL, content="x")
    await db_client.update("bus_messages", {"message_id": mid}, {"segments": "{not json"})

    msgs = await bus.get_messages(CHANNEL, limit=10)
    assert msgs[-1].segments is None
    assert msgs[-1].content == "x"


def test_the_bus_signature_appends_rather_than_inserts():
    """`send_message` has positional callers. A parameter added in the middle
    would silently rebind every one of them — this codebase has already paid
    that exact tax once, on ContextRuntime.
    """
    import inspect

    from xyz_agent_context.message_bus.local_bus import LocalMessageBus

    params = list(inspect.signature(LocalMessageBus.send_message).parameters)
    assert params.index("segments") == len(params) - 1


# ── the wiring, end to end ──────────────────────────────────────────────────
#
# Every test above stubs one side or the other. The seam between them is where
# this feature would silently do nothing: my first attempt read `collection` in
# a scope that had no such name, and `getattr(collection, "segments", None)`
# would have swallowed that into a permanent None — feature dead, no error, no
# log, tests green.


def test_the_team_reply_hands_its_segments_to_the_bus():
    """The trigger must pass what the collector preserved, and it must come from
    a real return value rather than an attribute lookup that can quietly miss."""
    import inspect

    from xyz_agent_context.message_bus import message_bus_trigger as mod

    src = inspect.getsource(mod.MessageBusTrigger._handle_channel_batch)
    assert "segments=turn.segments" in src
    assert "getattr(collection" not in src


def test_invoke_runtime_returns_the_segments_it_collected():
    """A FIELD on TurnResult, not a third tuple element.

    It began as one, and the merge with the turn-result refactor is exactly why
    it should not have been: a tuple that grows rebinds every positional unpack
    silently, and every caller had to be found by hand. A field with a default
    is invisible to callers that do not want it.
    """
    import inspect

    from xyz_agent_context.message_bus.message_bus_trigger import MessageBusTrigger

    src = inspect.getsource(MessageBusTrigger._invoke_runtime)
    assert "segments=collection.segments" in src


@pytest.mark.asyncio
async def test_the_route_passes_segments_to_the_panel(db_client):
    """A column the API does not return is a column the UI cannot render."""
    import inspect

    from backend.routes import teams as mod

    src = inspect.getsource(mod)
    assert '"segments": m.segments' in src


def test_bus_messages_are_never_updated_in_place():
    """The assumption the team room's incremental polling rests on.

    The panel used to refetch all 200 messages every 3 seconds, which was
    idempotent by construction. Sending `since` and merging is only equivalent
    while a message, once written, never changes: an append-only merge cannot
    see an edit, so an edit would silently never reach the screen.

    Asserted against the source rather than by trying to perform an edit,
    because the property is "no such path exists" — a behavioural test can only
    show that the paths I happened to think of do not do it.

    If this ever needs to change, `mergeTeamMessages` is the file that has to
    change with it.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    offenders = []
    for path in list((root / "src").rglob("*.py")) + list((root / "backend").rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if 'update("bus_messages"' in text or "update('bus_messages'" in text:
            offenders.append(str(path.relative_to(root)))

    assert offenders == [], (
        f"bus_messages is updated in place by {offenders}; the team room's "
        f"incremental merge assumes append-only and would never show the edit"
    )
