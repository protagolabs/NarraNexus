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
    """The trigger passes the boundary to the post, from a real value.

    Reads the source because the post moved inside the turn and the deliverer is
    a closure — but the BEHAVIOUR is covered end to end by
    `test_a_real_team_turn_stores_its_segments` below, which is the test that
    actually goes red. What is pinned here is the shape that has bitten twice:
    a `getattr` fallback, which turns "the name is wrong" into a permanent None
    with no error and no log.
    """
    import inspect

    from xyz_agent_context.message_bus import message_bus_trigger as mod

    src = inspect.getsource(mod.MessageBusTrigger._handle_channel_batch)
    assert "segments=joined_segments(live_segments)" in src
    assert "getattr(collection" not in src


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


# ── the seam, driven for real ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_real_team_turn_stores_its_segments(db_client, monkeypatch):
    """A team reply posted by the TRIGGER carries its segments into the row.

    Every other test here either calls `send_message` directly or reads the
    trigger's source. Both stayed green through a merge in which the reply path
    raised `TypeError` on every single team turn: a new `_post_to_room` funnel
    landed on one branch, segments on another, and the funnel did not accept
    them. The caller's own "the room will never show this reply" handler caught
    it and announced a delivery failure instead — so the room lost every reply,
    loudly, while the source assertions still matched.

    This is the only test that would have gone red on its own.
    """
    from xyz_agent_context.message_bus.message_bus_trigger import (
        MessageBusTrigger,
        TurnResult,
    )
    from xyz_agent_context.schema.team_schema import TEAM_ROOM_OWNER_PREFIX

    room = "ch_seam"
    await db_client.insert("bus_channels", {
        "channel_id": room, "channel_type": "group",
        "created_by": f"{TEAM_ROOM_OWNER_PREFIX}t_seam", "name": "Desk",
    })
    await db_client.insert("teams", {
        "team_id": "t_seam", "owner_user_id": "usr_1", "name": "Desk",
        "lead_agent_id": "agent_me",
    })
    await db_client.insert("bus_channel_members", {"channel_id": room, "agent_id": "agent_me"})
    await db_client.insert("agents", {
        "agent_id": "agent_me", "agent_name": "Mia", "created_by": "usr_1",
    })

    async def _get_db():
        return db_client

    monkeypatch.setattr("xyz_agent_context.utils.db.db_factory.get_db_client", _get_db)

    trigger = MessageBusTrigger(bus=LocalMessageBus(backend=db_client._backend))

    async def _invoke(**kwargs):
        # Stands in for `collect_run`: it fills the caller's sink as the run
        # streams and hands the reply to the deliverer, which is what makes the
        # boundary available at post time. The post is inside the turn now, so a
        # stub that only returns a TurnResult posts nothing.
        sink = kwargs.get("segments_sink")
        if sink is not None:
            sink.extend([
                {"kind": "monologue", "parts": ["thinking"]},
                {"kind": "reply", "parts": ["answering"]},
            ])
        cb = kwargs.get("on_plain_text_delivery")
        if cb is not None:
            await cb("thinkinganswering")
        return TurnResult(
            text="thinkinganswering",
            event_id="evt_seam",
            segments=[
                {"kind": "monologue", "text": "thinking"},
                {"kind": "reply", "text": "answering"},
            ],
        )

    trigger._invoke_runtime = _invoke  # type: ignore[method-assign]

    await trigger._bus.send_message(
        from_agent="usr_1", to_channel=room, content="anyone?", mentions=["agent_me"]
    )
    await trigger._process_agent("agent_me")

    rows = await db_client.execute(
        "SELECT content, segments FROM bus_messages "
        "WHERE channel_id = %s AND from_agent = %s",
        (room, "agent_me"),
        fetch=True,
    )
    assert len(rows) == 1, "the reply did not reach the room at all"
    assert json.loads(rows[0]["segments"]) == [
        {"kind": "monologue", "text": "thinking"},
        {"kind": "reply", "text": "answering"},
    ]


def test_the_room_funnel_carries_everything_a_room_caller_can_send():
    """`_post_to_room` must not fall behind `send_message`.

    The funnel lists its parameters explicitly, on purpose: `**kwargs` hides a
    misspelled keyword from the type checker and only surfaces it as a runtime
    TypeError. The cost of that choice is that the list can fall behind — and it
    did, for one merge, in the worst possible way: `segments` was added to the
    reply on one branch while the funnel was introduced on another, git merged
    both cleanly, and every team turn then raised TypeError inside the poster,
    was caught by the caller's "the room will never show this" handler, and
    posted a delivery-failure notice instead of the agent's words.

    So the drift is asserted rather than trusted to a comment. The exemptions
    are the parameters a ROOM post has no use for, each for its own reason —
    listing them here is what makes adding a genuinely room-relevant parameter
    fail this test instead of failing in production.
    """
    import inspect

    from xyz_agent_context.message_bus.cloud_bus import CloudMessageBus
    from xyz_agent_context.message_bus.local_bus import LocalMessageBus
    from xyz_agent_context.message_bus.message_bus_service import MessageBusService
    from xyz_agent_context.message_bus.message_bus_trigger import MessageBusTrigger

    # Not routed through the room funnel, and why:
    #   attachments        — the trigger never posts files; the user's uploads
    #                        come in through the REST route, not from a turn.
    #   sender_turn_source — describes the SENDER's errand, which a room post
    #                        does not have: the room is the surface, not a peer.
    #   root_run_id        — cascade lineage, stamped by the agent's own bus
    #                        sends; a room reply's lineage rides the trigger.
    #   routed_by          — records why `mentions` holds what it does, and the
    #                        funnel's callers compute mentions themselves.
    exempt = {"attachments", "sender_turn_source", "root_run_id", "routed_by"}

    # Anchored on the PROTOCOL, not on one implementation: `_post_to_room` calls
    # `self._bus.send_message`, and the bus in production is whichever one the
    # deployment wired up. An implementation that gained a parameter the protocol
    # did not would be invisible to a test that only reads the local one.
    def _params(fn) -> set:
        return set(inspect.signature(fn).parameters) - {"self"}

    sends = _params(MessageBusService.send_message)
    funnel = _params(MessageBusTrigger._post_to_room)

    # And the implementations must not drift from the protocol either — the two
    # `# Pinned by test_team_message_segments` comments in local_bus and
    # cloud_bus were, until this line, promises nothing checked.
    for impl in (LocalMessageBus, CloudMessageBus):
        assert _params(impl.send_message) == sends, (
            f"{impl.__name__}.send_message has drifted from MessageBusService"
        )

    missing = sends - funnel - exempt
    assert not missing, (
        f"{sorted(missing)} can be sent to a channel but cannot be sent to a ROOM. "
        f"Either add them to `_post_to_room` or name them in this test's exemption "
        f"list with the reason — the one time this drifted, every team reply became "
        f"a delivery-failure notice."
    )
