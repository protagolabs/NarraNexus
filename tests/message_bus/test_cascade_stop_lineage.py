"""
@file_name: test_cascade_stop_lineage.py
@date: 2026-08-07
@description: The trigger tree — how a stop reaches runs the owner never saw.

A long task rarely stays in one run: the agent asks a peer, that peer asks a
third. Stopping only the clicked run leaves those branches burning tokens and,
worse, their queued messages wake NEW runs — the owner presses stop and watches
fresh work appear.

The tree is a flat inherited LABEL (``events.root_run_id``), not parent/child
edges: the only question ever asked is "which runs belong to this tree", and a
label answers it in one indexed query at any depth.

Pinned here:
  * a root run labels itself; a caused run inherits
  * the label survives the agent→agent hop (the one place it could be lost:
    the message is written by an MCP tool in another process)
  * queued messages of a stopped tree stop waking runs
  * a NULL label is never treated as "same tree" (that would match every
    legacy row in the table)
"""
from __future__ import annotations

import pytest

from xyz_agent_context.message_bus.local_bus import LocalMessageBus
from xyz_agent_context.utils.timezone import utc_now


async def _seed_channel(db, channel_id="ch_1", agents=("agent_a", "agent_b")):
    await db.insert("bus_channels", {
        "channel_id": channel_id, "name": "room", "channel_type": "group",
        "created_by": "team_1",
    })
    for a in agents:
        await db.insert("bus_channel_members", {"channel_id": channel_id, "agent_id": a})
        await db.insert("agents", {"agent_id": a, "agent_name": a, "created_by": "user_x"})


async def _seed_run(db, event_id, *, root, state="running", cancel_at=None):
    await db.insert("events", {
        "event_id": event_id, "trigger": "message_bus", "trigger_source": "message_bus",
        "agent_id": "agent_a", "user_id": "user_x", "state": state,
        "started_at": utc_now(), "last_event_at": utc_now(),
        "root_run_id": root, "cancel_requested_at": cancel_at,
    })


# ── the label itself ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_root_run_labels_itself(db_client):
    """No inherited root → the run IS a root and stamps its own id.

    Locked because the alternative (leaving it NULL for roots) would make the
    very first run of every tree unstoppable-as-a-tree.
    """
    from xyz_agent_context.agent_runtime.run_recorder import RunRecorder

    await _seed_run(db_client, "evt_root", root=None, state="completed")
    rec = RunRecorder(db=db_client)
    await rec._bind_run_id("evt_root")
    try:
        row = await db_client.get_one("events", {"event_id": "evt_root"})
        assert row["root_run_id"] == "evt_root"
    finally:
        await rec.finalize("completed")


@pytest.mark.asyncio
async def test_a_caused_run_inherits_the_tree(db_client):
    from xyz_agent_context.agent_runtime.run_recorder import RunRecorder

    await _seed_run(db_client, "evt_child", root=None, state="completed")
    rec = RunRecorder(db=db_client, inherited_root_run_id="evt_root")
    await rec._bind_run_id("evt_child")
    try:
        row = await db_client.get_one("events", {"event_id": "evt_child"})
        assert row["root_run_id"] == "evt_root"
    finally:
        await rec.finalize("completed")


@pytest.mark.asyncio
async def test_the_client_reads_the_tree_off_trigger_extra_data(db_client):
    """The seam between trigger and recorder — a rename on either side would
    silently orphan every caused run, and nothing else would fail."""
    from xyz_agent_context.agent_runtime.client import _inherited_root_run_id

    assert _inherited_root_run_id({"trigger_extra_data": {"root_run_id": "evt_r"}}) == "evt_r"
    assert _inherited_root_run_id({"trigger_extra_data": {}}) is None
    assert _inherited_root_run_id({}) is None
    # A run that starts a tree passes "" — it must not be read as a tree named "".
    assert _inherited_root_run_id({"trigger_extra_data": {"root_run_id": ""}}) is None


# ── the hop where lineage could break ───────────────────────────────────────

@pytest.mark.asyncio
async def test_the_label_survives_the_agent_to_agent_hop(db_client):
    """An agent asking a peer writes a NEW message; the peer's run learns the
    tree only from that row. This is the one hop where the chain can break."""
    await _seed_channel(db_client)
    bus = LocalMessageBus(backend=db_client._backend)

    msg_id = await bus.send_message(
        from_agent="agent_a", to_channel="ch_1", content="@b help",
        root_run_id="evt_root",
    )

    row = await db_client.get_one("bus_messages", {"message_id": msg_id})
    assert row["root_run_id"] == "evt_root"
    # And it must come back out on the model the trigger reads.
    pending = await bus.get_pending_messages("agent_b")
    assert [m.root_run_id for m in pending] == ["evt_root"]


# ── queued work of a stopped tree ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_queued_messages_of_a_stopped_tree_stop_waking_runs(db_client):
    """Stopping the running turns is not enough: their queued follow-ups would
    start new runs on the next poll, and the owner would see fresh work appear
    right after pressing stop."""
    await _seed_channel(db_client)
    await _seed_run(db_client, "evt_root", root="evt_root", cancel_at=utc_now())
    bus = LocalMessageBus(backend=db_client._backend)

    await bus.send_message(
        from_agent="agent_a", to_channel="ch_1", content="queued work",
        root_run_id="evt_root",
    )

    assert await bus.get_pending_messages("agent_b") == []


@pytest.mark.asyncio
async def test_a_live_tree_still_delivers(db_client):
    await _seed_channel(db_client)
    await _seed_run(db_client, "evt_root", root="evt_root", cancel_at=None)
    bus = LocalMessageBus(backend=db_client._backend)

    await bus.send_message(
        from_agent="agent_a", to_channel="ch_1", content="carry on",
        root_run_id="evt_root",
    )

    assert len(await bus.get_pending_messages("agent_b")) == 1


@pytest.mark.asyncio
async def test_an_unlabelled_message_is_never_suppressed(db_client):
    """A user's message and every pre-column row carry NULL. If NULL matched
    "the tree being stopped", one stop would mute the whole table."""
    await _seed_channel(db_client)
    await _seed_run(db_client, "evt_root", root="evt_root", cancel_at=utc_now())
    bus = LocalMessageBus(backend=db_client._backend)

    await bus.send_message(from_agent="agent_a", to_channel="ch_1", content="hello")

    pending = await bus.get_pending_messages("agent_b")
    assert len(pending) == 1
    assert pending[0].root_run_id is None


@pytest.mark.asyncio
async def test_a_stopped_tree_whose_root_already_finished_still_suppresses(db_client):
    """The delegating shape: the root run ENDS before the work does.

    An agent that hands work to a peer typically finishes its own turn right
    after sending — "fire it off, end my turn, they'll answer later". So by the
    time the owner sees the peer running and presses stop, the ROOT row is
    already `completed` and never receives a flag (settled rows are
    deliberately not flagged — a terminal row must not carry a pending stop).

    A suppression predicate that only looks at the root row therefore reads
    "nothing was stopped" and keeps waking new runs — the whack-a-mole this
    feature exists to kill, in its most common form. The question the filter
    must ask is "did ANYONE in this tree get stopped", not "did the root".
    """
    await _seed_channel(db_client)
    # Root finished long ago, carries no flag.
    await _seed_run(db_client, "evt_root", root="evt_root", state="completed")
    # Its child is the one still running, and the one the owner stopped.
    await _seed_run(db_client, "evt_child", root="evt_root", cancel_at=utc_now())
    bus = LocalMessageBus(backend=db_client._backend)

    await bus.send_message(
        from_agent="agent_a", to_channel="ch_1", content="queued follow-up",
        root_run_id="evt_root",
    )

    assert await bus.get_pending_messages("agent_b") == []
