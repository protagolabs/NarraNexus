"""
@file_name: test_multipart_messages.py
@date: 2026-09-09
@description: A long A2A message arrives whole, and a silent recipient is not
poked into a resend loop.

8/31 user report: replies past ~4-5k characters were "cut off", the send said
success, the recipient's turn was empty, and "This turn ended without
delivering a reply" fired again and again. Two mechanisms, two guards:

* the bus never truncates — a message that does not fit one call travels as
  ordered PARTS and the recipient's trigger hands its turn ONE reassembled
  message (12k characters round-trip byte-for-byte); an incomplete group is
  held (no turn, no ack), an out-of-order or oversize part is refused, and a
  group whose sender died is delivered with an explicit marker after the grace;
* a recipient that goes silent on the SAME content twice wakes the sender once,
  not every time.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from narranexus.platform.message_bus import multipart
from narranexus.platform.message_bus.delivery_notice import UNDELIVERED_MSG_TYPE
from narranexus.platform.message_bus.local_bus import LocalMessageBus
from narranexus.platform.message_bus.message_bus_trigger import (
    MessageBusTrigger,
    TurnResult,
)
from narranexus.platform.message_bus.schemas import BusMessage
from narranexus.platform.repository.bus_delivery_receipt_repository import (
    RECEIPT_PROCESSED,
    BusDeliveryReceiptRepository,
)
from narranexus_plugins.message_bus_module._message_bus_mcp_tools import (
    MAX_BUS_MESSAGE_BYTES,
    register_message_bus_mcp_tools,
)

OWNER = "usr_multipart"
A, B = "agent_mp_a", "agent_mp_b"

# 12k characters, three distinct thirds so a wrong join order or a lost part
# cannot pass as "roughly the same text".
LONG = "".join(f"[{i:05d}]" for i in range(1800)) + "END"
assert len(LONG) > 12_000
PARTS = [LONG[:4000], LONG[4000:8000], LONG[8000:]]


def _patch_db(monkeypatch, db_client):
    async def _async_db():
        return db_client

    monkeypatch.setattr("narranexus.platform.utils.db.db_factory.get_db_client", _async_db)
    monkeypatch.setattr("narranexus.platform.utils.get_db_client", _async_db)


async def _agent(db, agent_id, owner=OWNER):
    await db.insert("agents", {"agent_id": agent_id, "agent_name": agent_id, "created_by": owner})


def _tools(db_client):
    bus = LocalMessageBus(backend=db_client._backend)
    captured: dict = {}

    class _Stub:
        def tool(self, *_a, **_k):
            def _wrap(fn):
                captured[fn.__name__] = fn
                return fn
            return _wrap

    async def _bus():
        return bus

    register_message_bus_mcp_tools(_Stub(), _bus)
    return captured, bus


def _capturing_runtime(monkeypatch, trigger, result: TurnResult):
    calls: list = []

    async def _fake(*_a, **k):
        calls.append(k)
        on_event_id = k.get("on_event_id")
        if on_event_id is not None:
            await on_event_id(result.event_id or "evt_stub")
        return result

    monkeypatch.setattr(trigger, "_invoke_runtime", _fake)
    return calls


async def _send_parts(tools, parts, upto=None):
    ids = []
    for i, text in enumerate(parts[:upto], start=1):
        out = await tools["message_agent"](
            agent_id=A, to=B, text=text, part_index=i, part_count=len(parts)
        )
        assert out["success"] is True, out
        ids.append(out["message_id"])
    return ids


# ── round trip ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_12k_message_sent_in_parts_reaches_the_turn_intact(db_client, monkeypatch):
    _patch_db(monkeypatch, db_client)
    await _agent(db_client, A)
    await _agent(db_client, B)
    tools, bus = _tools(db_client)
    trigger = MessageBusTrigger(bus=bus)
    calls = _capturing_runtime(monkeypatch, trigger, TurnResult(text="", event_id="e", delivered=True))

    ids = await _send_parts(tools, PARTS)
    channel_id = (await db_client.get_one("bus_messages", {"message_id": ids[0]}))["channel_id"]

    assert await trigger._process_lane(B, channel_id) is True
    assert len(calls) == 1
    prompt = calls[0]["prompt"]
    assert LONG in prompt                      # every byte, in order, once
    assert prompt.count("[00000]") == 1
    assert calls[0]["retrieval_anchor"].count("[From agent") == 1

    # Every part row got the receipt of the one turn that consumed them.
    repo = BusDeliveryReceiptRepository(db_client)
    for mid in ids:
        assert (await repo.get(mid, B))["status"] == RECEIPT_PROCESSED
    # And the cursor passed the LAST part: nothing is pending any more.
    assert await bus.get_pending_messages(B, channel_id=channel_id) == []


@pytest.mark.asyncio
async def test_an_incomplete_group_is_held_without_a_turn_or_an_ack(db_client, monkeypatch):
    _patch_db(monkeypatch, db_client)
    await _agent(db_client, A)
    await _agent(db_client, B)
    tools, bus = _tools(db_client)
    trigger = MessageBusTrigger(bus=bus)
    calls = _capturing_runtime(monkeypatch, trigger, TurnResult(text="", event_id="e", delivered=True))

    ids = await _send_parts(tools, PARTS, upto=2)
    channel_id = (await db_client.get_one("bus_messages", {"message_id": ids[0]}))["channel_id"]

    assert await trigger._process_lane(B, channel_id) is False
    assert calls == []
    # Still pending — the last part's wake will bring the lane back.
    assert len(await bus.get_pending_messages(B, channel_id=channel_id)) == 2

    # The sender starts over at 1/3. A new part 1 opens a NEW group, which
    # the write edge will now follow — the old two-part group can never be
    # continued, so it is superseded: delivered as what arrived, with its
    # marker, in the same turn as the complete new one. No hold, no wait.
    await _send_parts(tools, PARTS)
    assert await trigger._process_lane(B, channel_id) is True
    prompt = calls[0]["prompt"]
    assert LONG in prompt
    assert prompt.count("[00000]") == 2          # old fragment + whole message
    assert "part(s) 3 never arrived" in prompt   # and the fragment says so


@pytest.mark.asyncio
async def test_a_stale_incomplete_group_is_delivered_with_a_marker(db_client, monkeypatch):
    """The sender's turn died between parts. After the grace the recipient gets
    what arrived, and is TOLD what is missing — never silently, never never."""
    _patch_db(monkeypatch, db_client)
    await _agent(db_client, A)
    await _agent(db_client, B)
    tools, bus = _tools(db_client)
    trigger = MessageBusTrigger(bus=bus)
    calls = _capturing_runtime(monkeypatch, trigger, TurnResult(text="", event_id="e", delivered=True))
    ids = await _send_parts(tools, PARTS, upto=2)
    channel_id = (await db_client.get_one("bus_messages", {"message_id": ids[0]}))["channel_id"]

    monkeypatch.setattr(multipart, "PART_ASSEMBLY_GRACE_SECONDS", 0)
    # `assemble` reads the module constant as its default at call time only if
    # we route through it; pin the trigger's call by patching the default.
    monkeypatch.setattr(
        "narranexus.platform.message_bus.message_bus_trigger.assemble_parts",
        lambda msgs: multipart.assemble(msgs, grace_seconds=0),
    )

    assert await trigger._process_lane(B, channel_id) is True
    prompt = calls[0]["prompt"]
    assert PARTS[0] + PARTS[1] in prompt
    assert "part(s) 3 never arrived" in prompt


# ── the write edge refuses what it could not reassemble ─────────────────────


@pytest.mark.asyncio
async def test_an_out_of_order_part_is_refused_and_not_stored(db_client, monkeypatch):
    _patch_db(monkeypatch, db_client)
    await _agent(db_client, A)
    await _agent(db_client, B)
    tools, _ = _tools(db_client)

    out = await tools["message_agent"](agent_id=A, to=B, text="x", part_index=2, part_count=3)

    assert out["success"] is False
    assert "starting at 1/3" in out["error"]
    assert await db_client.get("bus_messages", {"from_agent": A}) == []


@pytest.mark.asyncio
async def test_an_oversize_single_message_is_refused_not_truncated(db_client, monkeypatch):
    _patch_db(monkeypatch, db_client)
    await _agent(db_client, A)
    await _agent(db_client, B)
    tools, _ = _tools(db_client)

    out = await tools["message_agent"](agent_id=A, to=B, text="x" * (MAX_BUS_MESSAGE_BYTES + 1))

    assert out["success"] is False
    assert "part_index/part_count" in out["error"]
    assert await db_client.get("bus_messages", {"from_agent": A}) == []
    # And exactly at the limit is fine.
    ok = await tools["message_agent"](agent_id=A, to=B, text="x" * MAX_BUS_MESSAGE_BYTES)
    assert ok["success"] is True


def test_assemble_leaves_ordinary_batches_alone():
    msgs = [
        BusMessage(message_id=f"m{i}", channel_id="c", from_agent="a", content=f"t{i}")
        for i in range(3)
    ]
    out, hold = multipart.assemble(msgs)
    assert hold is False and out == msgs


def test_assemble_merges_in_place_and_keeps_the_last_created_at():
    now = datetime.now(timezone.utc)
    t = lambda s: (now - timedelta(seconds=s)).isoformat()  # noqa: E731
    msgs = [
        BusMessage(message_id="p1", channel_id="c", from_agent="a", content="AB",
                   part_index=1, part_count=2, part_group="p1", created_at=t(30),
                   attachments=[{"file_id": "att_1"}]),
        BusMessage(message_id="o", channel_id="c", from_agent="z", content="other", created_at=t(20)),
        BusMessage(message_id="p2", channel_id="c", from_agent="a", content="CD",
                   part_index=2, part_count=2, part_group="p1", created_at=t(10)),
    ]
    out, hold = multipart.assemble(msgs, now=now)
    assert hold is False
    assert [m.message_id for m in out] == ["p1", "o"]
    whole = out[0]
    assert whole.content == "ABCD"
    assert whole.created_at == t(10)
    assert whole.part_message_ids == ["p1", "p2"]
    assert whole.attachments == [{"file_id": "att_1"}]


# ── the resend guard ────────────────────────────────────────────────────────


async def _dm(db_client, tools, text):
    out = await tools["message_agent"](agent_id=A, to=B, text=text)
    row = await db_client.get_one("bus_messages", {"message_id": out["message_id"]})
    return BusMessage(
        message_id=row["message_id"], channel_id=row["channel_id"],
        from_agent=A, content=row["content"], created_at=row["created_at"],
    )


@pytest.mark.asyncio
async def test_a_silent_recipient_wakes_the_sender_once_per_content(db_client, monkeypatch):
    _patch_db(monkeypatch, db_client)
    await _agent(db_client, A)
    await _agent(db_client, B)
    tools, bus = _tools(db_client)
    trigger = MessageBusTrigger(bus=bus)
    _capturing_runtime(monkeypatch, trigger, TurnResult(text="", event_id="e"))

    first = await _dm(db_client, tools, "please draft the report")
    await trigger._handle_channel_batch(B, first.channel_id, [first], first, channel_owner=A)
    notices = await db_client.get("bus_messages", {"channel_id": first.channel_id, "msg_type": UNDELIVERED_MSG_TYPE})
    assert len(notices) == 1 and A in (notices[0]["mentions"] or "")

    # The sender, woken by that notice, sends the same thing again.
    resend = await _dm(db_client, tools, "please  draft the report")
    await trigger._handle_channel_batch(B, resend.channel_id, [resend], resend, channel_owner=A)
    notices = await db_client.get("bus_messages", {"channel_id": first.channel_id, "msg_type": UNDELIVERED_MSG_TYPE})
    assert len(notices) == 1, "the same silence must not wake the sender twice"

    # A genuinely different question is a fresh silence and IS announced.
    other = await _dm(db_client, tools, "different question entirely")
    await trigger._handle_channel_batch(B, other.channel_id, [other], other, channel_owner=A)
    notices = await db_client.get("bus_messages", {"channel_id": first.channel_id, "msg_type": UNDELIVERED_MSG_TYPE})
    assert len(notices) == 2
