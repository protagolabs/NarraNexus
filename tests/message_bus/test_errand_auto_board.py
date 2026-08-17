"""
@file_name: test_errand_auto_board.py
@author:
@date: 2026-08-14
@description: The errand layer — a hand-off gets onto the board without anyone
              remembering to put it there.

The work board (#259) can already notice a stalled task, tighten its cadence
and chase the assignee. Its ENTRANCE, though, was `work_add_item`: a tool the
Leader has to remember to call. So the whole guard rested on model obedience —
the dependency iron rule #15 exists to keep off correctness-critical paths —
and the founding case walks straight through it: nobody called the tool during
the Dunhuang chain, so the board was empty and patrol had nothing to sweep.

This is the message-level half the owner specified on 2026-08-07: "an errand is
a MESSAGE-level fact recorded automatically (A @ B opens it, B's reply closes
it)". Opening without closing would be strictly worse than nothing — every
casual "@Bruno 你怎么看" would pile onto the board and patrol would chase it
forever — so the two halves ship together and are tested together.

The one rule that carries the incident: **a promise does not close an errand.**
"收到，开始处理……完成后交付 @A4" is the exact text that ended the Dunhuang run,
and treating it as delivery would hand the guard back to the failure it exists
to catch.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.message_bus.errand import (
    close_delivered_errands,
    is_promise_only,
    record_handoffs,
)
from xyz_agent_context.repository.team_work_repository import TeamWorkItemRepository
from xyz_agent_context.schema.team_work_schema import WorkItemOrigin, WorkItemStatus


TEAM = "team_dunhuang"
CHANNEL = "ch_dunhuang"
LEAD = "agent_leader"
A3 = "agent_a3"
A4 = "agent_a4"

# The message that ended the real run (evt_41c581a8fe8a4bac), shortened.
PROMISE = "收到，开始处理。……完成后产出 03_normalised.json 并交付 @A4"


@pytest.fixture
def repo(db_client):
    return TeamWorkItemRepository(db_client)


async def _open_errand(db, *, from_agent, to_agent, text="handle the OCR output",
                       message_id="msg_1"):
    return await record_handoffs(
        db,
        team_id=TEAM,
        channel_id=CHANNEL,
        from_agent=from_agent,
        mentions=[to_agent],
        text=text,
        message_id=message_id,
        root_run_id="evt_root",
    )


# ===========================================================================
# Opening
# ===========================================================================

@pytest.mark.asyncio
async def test_an_at_mention_opens_an_errand_nobody_had_to_declare(db_client, repo):
    """The entrance that does not depend on the Leader remembering a tool."""
    opened = await _open_errand(db_client, from_agent=LEAD, to_agent=A3)

    assert len(opened) == 1
    item = await repo.get(opened[0])
    assert item is not None
    assert item.assignee_id == A3
    assert item.created_by == LEAD
    assert item.status == WorkItemStatus.IN_PROGRESS
    assert item.origin == WorkItemOrigin.AUTO
    assert item.source_message_id == "msg_1"
    assert item.root_run_id == "evt_root"


@pytest.mark.asyncio
async def test_the_title_is_the_ask_without_the_at_noise(db_client, repo):
    """The board is read by a model every turn; `@A4 @A5 do the thing` as a
    title spends its budget on markup."""
    opened = await _open_errand(
        db_client, from_agent=LEAD, to_agent=A3,
        text="@A3 normalise the OCR output into 03_normalised.json\nthen tell me",
    )

    item = await repo.get(opened[0])
    assert item.title.startswith("normalise the OCR output")
    assert "@A3" not in item.title
    assert "\n" not in item.title


@pytest.mark.asyncio
async def test_the_same_message_never_opens_twice(db_client, repo):
    """The poll loop can re-deliver, and a retried post keeps its message id.

    Without this the board grows a duplicate on every retry and patrol chases
    the same hand-off several times over.
    """
    first = await _open_errand(db_client, from_agent=LEAD, to_agent=A3)
    again = await _open_errand(db_client, from_agent=LEAD, to_agent=A3)

    assert len(first) == 1
    assert again == []
    assert len(await repo.list_active(TEAM)) == 1


@pytest.mark.asyncio
async def test_broadcast_and_self_mention_are_not_hand_offs(db_client, repo):
    """`@everyone` addresses a room, not a person — nobody is late on it.

    A self-mention is not an assignment either, and would make an agent chase
    itself on the next patrol.
    """
    assert await record_handoffs(
        db_client, team_id=TEAM, channel_id=CHANNEL, from_agent=LEAD,
        mentions=["@everyone"], text="standup in 5", message_id="m_b",
        root_run_id="",
    ) == []
    assert await record_handoffs(
        db_client, team_id=TEAM, channel_id=CHANNEL, from_agent=LEAD,
        mentions=[LEAD], text="note to self", message_id="m_s", root_run_id="",
    ) == []
    assert await repo.list_active(TEAM) == []


@pytest.mark.asyncio
async def test_one_post_can_hand_off_to_several_people(db_client, repo):
    """Each assignee is late on their own, so each gets an item."""
    opened = await record_handoffs(
        db_client, team_id=TEAM, channel_id=CHANNEL, from_agent=LEAD,
        mentions=[A3, A4], text="split the batch between you",
        message_id="m_multi", root_run_id="",
    )

    assert len(opened) == 2
    assert {i.assignee_id for i in await repo.list_active(TEAM)} == {A3, A4}


# ===========================================================================
# Closing — and the one case that must NOT close
# ===========================================================================

@pytest.mark.asyncio
async def test_delivering_closes_the_errand(db_client, repo):
    """The other half. Without it the board only ever grows."""
    opened = await _open_errand(db_client, from_agent=LEAD, to_agent=A3)

    closed = await close_delivered_errands(
        db_client, team_id=TEAM, channel_id=CHANNEL, agent_id=A3,
        text="03_normalised.json is in the shared folder — 1,204 pages.",
    )

    assert closed == opened
    assert (await repo.get(opened[0])).status == WorkItemStatus.DONE


@pytest.mark.asyncio
async def test_the_dunhuang_promise_does_not_close_anything(db_client, repo):
    """The founding case, as one assertion.

    A3 answered and the run ended `completed`. If that text closed the errand,
    the board would agree with the runtime that the work was done — and the
    platform would once again have no idea the pipeline had died.
    """
    opened = await _open_errand(db_client, from_agent=LEAD, to_agent=A3)

    closed = await close_delivered_errands(
        db_client, team_id=TEAM, channel_id=CHANNEL, agent_id=A3, text=PROMISE,
    )

    assert closed == []
    assert (await repo.get(opened[0])).status == WorkItemStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_a_promise_that_hands_on_leaves_both_links_watched(db_client, repo):
    """A3's own errand stays open AND A4's opens — the two facts the chain needs.

    This is the composed behaviour of the two halves on the real message, and
    the reason they cannot be reasoned about separately.
    """
    a3_item = (await _open_errand(db_client, from_agent=LEAD, to_agent=A3))[0]

    closed = await close_delivered_errands(
        db_client, team_id=TEAM, channel_id=CHANNEL, agent_id=A3, text=PROMISE,
    )
    handed_on = await record_handoffs(
        db_client, team_id=TEAM, channel_id=CHANNEL, from_agent=A3,
        mentions=[A4], text=PROMISE, message_id="msg_2", root_run_id="evt_root",
    )

    assert closed == []
    assert len(handed_on) == 1
    active = {i.assignee_id: i for i in await repo.list_active(TEAM)}
    assert set(active) == {A3, A4}
    assert active[A3].item_id == a3_item


@pytest.mark.asyncio
async def test_a_bare_acknowledgment_does_not_close_either(db_client, repo):
    """"收到" alone is the shortest form of the same lie."""
    opened = await _open_errand(db_client, from_agent=LEAD, to_agent=A3)

    for ack in ("收到", "好的，我来处理", "got it", "OK, on it"):
        assert await close_delivered_errands(
            db_client, team_id=TEAM, channel_id=CHANNEL, agent_id=A3, text=ack,
        ) == [], f"{ack!r} closed the errand"

    assert (await repo.get(opened[0])).status == WorkItemStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_a_tool_made_item_is_never_auto_closed(db_client, repo):
    """The Leader's own board entry is a TASK, and one task spans several
    errands (owner decision 2026-08-07). Closing it on the first message from
    the assignee would delete the layer that decision created.
    """
    item = await repo.create_item(
        team_id=TEAM, channel_id=CHANNEL, title="the whole OCR pipeline",
        created_by=LEAD, assignee_id=A3,
    )

    closed = await close_delivered_errands(
        db_client, team_id=TEAM, channel_id=CHANNEL, agent_id=A3,
        text="first batch done, 400 pages",
    )

    assert closed == []
    assert (await repo.get(item.item_id)).status == WorkItemStatus.IN_PROGRESS
    assert item.origin == WorkItemOrigin.TOOL


@pytest.mark.asyncio
async def test_closing_is_scoped_to_this_room(db_client, repo):
    """An agent in two teams must not close one room's errand by speaking in
    the other."""
    opened = await _open_errand(db_client, from_agent=LEAD, to_agent=A3)

    closed = await close_delivered_errands(
        db_client, team_id="team_other", channel_id="ch_other", agent_id=A3,
        text="done over here",
    )

    assert closed == []
    assert (await repo.get(opened[0])).status == WorkItemStatus.IN_PROGRESS


# ===========================================================================
# Wiring — the layer is useless if the delivery path never calls it
# ===========================================================================

@pytest.mark.asyncio
async def test_a_real_team_reply_records_its_errand(db_client, monkeypatch, repo):
    """Through `_handle_channel_batch`, not through the helpers directly.

    Every assertion above would still pass with the module unreferenced. This
    is the one that fails if the delivery path forgets to call it.
    """
    from xyz_agent_context.message_bus.local_bus import LocalMessageBus
    from xyz_agent_context.message_bus.message_bus_trigger import (
        TEAM_ROOM_OWNER_PREFIX,
        MessageBusTrigger,
        TurnResult,
    )
    from xyz_agent_context.message_bus.schemas import BusMessage

    async def _async_db():
        return db_client

    monkeypatch.setattr(
        "xyz_agent_context.utils.db.db_factory.get_db_client", _async_db
    )
    for aid, name in ((LEAD, "Leader"), (A4, "A4")):
        await db_client.insert(
            "agents", {"agent_id": aid, "agent_name": name, "created_by": "usr_1"}
        )
        await db_client.insert(
            "bus_channel_members", {"channel_id": CHANNEL, "agent_id": aid}
        )

    trigger = MessageBusTrigger(bus=LocalMessageBus(backend=db_client._backend))

    # A team reply is posted from INSIDE the turn (#291): the chat rows are
    # written before `run()` returns, so a post made afterwards cannot be
    # recorded as a reply. A stub therefore has to do what the runtime does —
    # report the run id, then hand the plain text to the deliverer — or it
    # exercises a path production no longer takes, which is exactly where the
    # errand hook now lives.
    async def _fake(*_a, **_k):
        result = TurnResult(text="OCR is normalised — @A4 take it from here",
                            event_id="evt_1")
        on_event_id = _k.get("on_event_id")
        if on_event_id is not None:
            await on_event_id(result.event_id or "evt_stub")
        deliver = _k.get("on_plain_text_delivery")
        if deliver is not None:
            await deliver(result.text)
        return result

    monkeypatch.setattr(trigger, "_invoke_runtime", _fake)

    msg = BusMessage(
        message_id="m_in", channel_id=CHANNEL, from_agent="usr_1",
        content="@Leader kick off", mentions=[LEAD],
    )
    await trigger._handle_channel_batch(
        LEAD, CHANNEL, [msg], msg,
        channel_owner=f"{TEAM_ROOM_OWNER_PREFIX}{TEAM}",
    )

    items = await repo.list_active(TEAM)
    assert [i.assignee_id for i in items] == [A4]
    assert items[0].origin == WorkItemOrigin.AUTO
    # Keyed to the message the room actually shows, so the row is traceable.
    assert items[0].source_message_id


@pytest.mark.asyncio
async def test_bookkeeping_never_breaks_a_delivered_reply(db_client, monkeypatch):
    """The reply is already in the room by the time this layer runs.

    Letting a board write fail the hop would trade a working delivery for
    bookkeeping — the opposite of the trade this whole feature is making.
    """
    from xyz_agent_context.message_bus.local_bus import LocalMessageBus
    from xyz_agent_context.message_bus.message_bus_trigger import (
        TEAM_ROOM_OWNER_PREFIX,
        MessageBusTrigger,
        TurnResult,
    )
    from xyz_agent_context.message_bus.schemas import BusMessage

    async def _async_db():
        return db_client

    monkeypatch.setattr(
        "xyz_agent_context.utils.db.db_factory.get_db_client", _async_db
    )
    await db_client.insert(
        "agents", {"agent_id": LEAD, "agent_name": "L", "created_by": "usr_1"}
    )

    async def _boom(*_a, **_k):
        raise RuntimeError("board is on fire")

    monkeypatch.setattr(
        "xyz_agent_context.message_bus.errand.record_handoffs", _boom
    )

    trigger = MessageBusTrigger(bus=LocalMessageBus(backend=db_client._backend))

    async def _fake(*_a, **_k):
        result = TurnResult(text="here is the answer", event_id="evt_1")
        on_event_id = _k.get("on_event_id")
        if on_event_id is not None:
            await on_event_id(result.event_id or "evt_stub")
        deliver = _k.get("on_plain_text_delivery")
        if deliver is not None:
            # The deliverer must report success: a book-keeping failure that
            # made this return False would file a landed reply as lost — the
            # very confusion this test exists to prevent.
            assert await deliver(result.text) is True
        return result

    monkeypatch.setattr(trigger, "_invoke_runtime", _fake)

    msg = BusMessage(
        message_id="m_in", channel_id=CHANNEL, from_agent="usr_1",
        content="@Leader hi", mentions=[LEAD],
    )
    await trigger._handle_channel_batch(
        LEAD, CHANNEL, [msg], msg,
        channel_owner=f"{TEAM_ROOM_OWNER_PREFIX}{TEAM}",
    )

    rows = await db_client.get("bus_messages", {"channel_id": CHANNEL})
    assert any(r["content"] == "here is the answer" for r in rows), (
        "a board failure swallowed the reply"
    )


# ===========================================================================
# The prompt half — reduces how often the guard is consulted, is not the guard
# ===========================================================================

def test_the_team_prompt_names_the_alternatives_to_a_promise():
    """A bare ban would make silence the compliant answer.

    That is not hypothetical: the 0802 WeChat report came from a protocol whose
    own logic made saying nothing correct. So the rule has to carry the exits —
    finish now, or say how far you got, or schedule it — and the test asserts
    the exits, not just the prohibition.
    """
    from xyz_agent_context.message_bus.message_bus_trigger import MessageBusTrigger
    from xyz_agent_context.message_bus.schemas import BusMessage

    trigger = MessageBusTrigger.__new__(MessageBusTrigger)
    msg = BusMessage(
        message_id="m1", channel_id=CHANNEL, from_agent="usr_u", content="status?"
    )
    text = trigger._build_team_prompt(
        LEAD, [msg],
        [{"agent_id": LEAD, "name": "Ana"}, {"agent_id": A3, "name": "A3"}],
        owner_user_id="usr_u", team_id=TEAM, trigger_messages=[msg],
        lead_agent_id=LEAD, work_items=[], bulletin=None,
    )

    assert "Do not promise future delivery" in text
    assert "ENDS your turn" in text
    # The three exits.
    assert "finish the work in THIS turn" in text
    assert "how far you got" in text
    assert "job_create" in text


def test_the_group_im_protocol_carries_the_same_rule():
    """The 1:1 protocol has had this line since the DM work; the group one did
    not, so the same promise was compliant in every group channel."""
    from xyz_agent_context.channel.channel_prompts import (
        COMMUNICATION_PROTOCOL_DIRECT,
        COMMUNICATION_PROTOCOL_GROUP,
    )

    assert "Do not promise future work" in COMMUNICATION_PROTOCOL_DIRECT
    assert "Do not promise future work" in COMMUNICATION_PROTOCOL_GROUP


# ===========================================================================
# The classifier, pinned directly
# ===========================================================================

def test_promise_detection_covers_both_languages_and_neither_direction_by_accident():
    """Kept narrow on purpose: this decides whether a guard stays armed.

    A false "promise" costs one patrol line; a false "delivery" costs the whole
    mechanism on exactly the message it was built for. So the bias is towards
    staying open, and the vocabulary is small enough to read in one screen.
    """
    for promise in (
        "收到，开始处理。……完成后产出 03_normalised.json 并交付 @A4",
        "好的，我稍后回来汇报",
        "let me look into it and get back to you",
        "I'll report back when it's ready",
        "收到",
        "on it",
    ):
        assert is_promise_only(promise) is True, promise

    for delivery in (
        "03_normalised.json is in the shared folder — 1,204 pages.",
        "找到 3 处错位，已在 notes.md 里逐条列出",
        "cannot do it: the scans for pages 40-60 are missing",
    ):
        assert is_promise_only(delivery) is False, delivery
