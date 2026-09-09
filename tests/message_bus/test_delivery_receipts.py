"""
@file_name: test_delivery_receipts.py
@date: 2026-09-09
@description: A bus sender gets a receipt, and hears when its message dies.

Upstream NetMindAI-Open/NarraNexus#106: "send success" meant "row inserted".
The Product Manager told its user "build is now in progress" while the Web
Developer's worker had crashed three times on that very message; the only
trace was an unread row in the recipient OWNER's inbox. The sender agent had
no way to know. Two surfaces close that:

* the send tool returns a `receipt` (accepted / held with the reason) — the
  pre-flight the sender's own turn can act on;
* the recipient's trigger keeps a `bus_delivery_receipts` row per message and,
  when the message is dropped for good, posts a notice into the conversation
  that MENTIONS the sender, so its next turn opens on the failure.

The regression guards matter as much: a team-room turn keeps writing no
receipts, and a user-sent DM never gets a sender notice (nobody to wake).
"""
from __future__ import annotations

import pytest

from narranexus.platform.message_bus.delivery_notice import DELIVERY_FAILED_MSG_TYPE
from narranexus.platform.message_bus.local_bus import LocalMessageBus
from narranexus.platform.message_bus.message_bus_trigger import (
    TEAM_ROOM_OWNER_PREFIX,
    MessageBusTrigger,
    TurnResult,
)
from narranexus.platform.message_bus.schemas import BusMessage
from narranexus.platform.repository.bus_delivery_receipt_repository import (
    RECEIPT_ACCEPTED,
    RECEIPT_DROPPED,
    RECEIPT_FAILED,
    RECEIPT_HELD,
    RECEIPT_PROCESSED,
    RECEIPT_RELAYED,
    RECEIPT_SILENT,
    BusDeliveryReceiptRepository,
)
from narranexus_plugins.message_bus_module._message_bus_mcp_tools import (
    register_message_bus_mcp_tools,
)

OWNER = "usr_receipt"
A, B = "agent_rcpt_a", "agent_rcpt_b"
DM = "ch_receipt_dm"


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


def _returns(monkeypatch, trigger, result: TurnResult):
    async def _fake(*_a, **_k):
        on_event_id = _k.get("on_event_id")
        if on_event_id is not None:
            await on_event_id(result.event_id or "evt_stub")
        return result

    monkeypatch.setattr(trigger, "_invoke_runtime", _fake)


def _boom(error_message: str):
    async def _raise(*args, **kwargs):
        raise RuntimeError(error_message)
    return _raise


# ── the send tool's receipt ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_message_agent_returns_an_accepted_receipt_and_books_it(db_client, monkeypatch):
    _patch_db(monkeypatch, db_client)
    await _agent(db_client, A)
    await _agent(db_client, B)
    tools, _ = _tools(db_client)

    out = await tools["message_agent"](agent_id=A, to=B, text="can you build it?")

    assert out["success"] is True
    assert out["receipt"]["status"] == RECEIPT_ACCEPTED
    row = await BusDeliveryReceiptRepository(db_client).get(out["message_id"], B)
    assert row is not None and row["status"] == RECEIPT_ACCEPTED
    assert row["from_agent"] == A


@pytest.mark.asyncio
async def test_message_agent_reports_held_when_the_recipient_is_paused(db_client, monkeypatch):
    """The recipient's circuit breaker is PAUSED (dead key): the message is
    queued, but the sender is told it will not run until that clears —
    instead of "success" and a promise to its user."""
    _patch_db(monkeypatch, db_client)
    await _agent(db_client, A)
    await _agent(db_client, B)
    await db_client.insert(
        "instance_agent_circuit_breaker",
        {"agent_id": B, "cb_status": "paused", "paused_reason": "auth"},
    )
    tools, _ = _tools(db_client)

    out = await tools["message_agent"](agent_id=A, to=B, text="can you build it?")

    assert out["success"] is True  # it IS queued
    assert out["receipt"]["status"] == RECEIPT_HELD
    assert "paused" in out["receipt"]["reason"]
    row = await BusDeliveryReceiptRepository(db_client).get(out["message_id"], B)
    assert row["status"] == RECEIPT_HELD


@pytest.mark.asyncio
async def test_message_agent_failure_carries_a_failed_receipt(db_client, monkeypatch):
    _patch_db(monkeypatch, db_client)
    await _agent(db_client, A)
    await _agent(db_client, B, owner="usr_other")
    tools, _ = _tools(db_client)

    out = await tools["message_agent"](agent_id=A, to=B, text="hi")

    assert out["success"] is False
    assert out["receipt"]["status"] == RECEIPT_FAILED
    assert "cross-user" in out["receipt"]["reason"]


# ── the trigger's side of the ledger ────────────────────────────────────────


async def _seed_dm(db_client, tools):
    out = await tools["message_agent"](agent_id=A, to=B, text="can you build it?")
    row = await db_client.get_one("bus_messages", {"message_id": out["message_id"]})
    return BusMessage(
        message_id=row["message_id"], channel_id=row["channel_id"],
        from_agent=A, content=row["content"], created_at=row["created_at"],
    )


@pytest.mark.parametrize(
    "result, expected",
    [
        (TurnResult(text="", event_id="evt", delivered=True), RECEIPT_PROCESSED),
        (TurnResult(text="told my owner", event_id="evt"), RECEIPT_RELAYED),
        (TurnResult(text="", event_id="evt"), RECEIPT_SILENT),
    ],
)
@pytest.mark.asyncio
async def test_trigger_stamps_the_outcome_of_the_turn(db_client, monkeypatch, result, expected):
    _patch_db(monkeypatch, db_client)
    await _agent(db_client, A)
    await _agent(db_client, B)
    tools, bus = _tools(db_client)
    msg = await _seed_dm(db_client, tools)
    trigger = MessageBusTrigger(bus=bus)
    _returns(monkeypatch, trigger, result)

    await trigger._handle_channel_batch(B, msg.channel_id, [msg], msg, channel_owner=A)

    row = await BusDeliveryReceiptRepository(db_client).get(msg.message_id, B)
    assert row["status"] == expected


@pytest.mark.asyncio
async def test_a_dropped_message_wakes_the_sender_with_the_redacted_reason(db_client, monkeypatch):
    _patch_db(monkeypatch, db_client)
    await _agent(db_client, A)
    await _agent(db_client, B)
    tools, bus = _tools(db_client)
    msg = await _seed_dm(db_client, tools)
    trigger = MessageBusTrigger(bus=bus)
    monkeypatch.setattr(
        trigger, "_invoke_runtime",
        _boom("No module named 'x.provider_resolver' key=sk-live-DEADBEEF0123456789"),
    )
    repo = BusDeliveryReceiptRepository(db_client)

    for attempt in (1, 2):
        await trigger._handle_channel_batch(B, msg.channel_id, [msg], msg, channel_owner=A)
        row = await repo.get(msg.message_id, B)
        assert (row["status"], row["attempts"]) == (RECEIPT_FAILED, attempt)
        # Below the poison threshold there is nothing final to tell the sender.
        assert await db_client.get("bus_messages", {"channel_id": msg.channel_id, "msg_type": DELIVERY_FAILED_MSG_TYPE}) == []

    await trigger._handle_channel_batch(B, msg.channel_id, [msg], msg, channel_owner=A)

    row = await repo.get(msg.message_id, B)
    assert (row["status"], row["attempts"]) == (RECEIPT_DROPPED, 3)
    assert "sk-live-DEADBEEF0123456789" not in (row["reason"] or "")
    notices = await db_client.get("bus_messages", {"channel_id": msg.channel_id, "msg_type": DELIVERY_FAILED_MSG_TYPE})
    assert len(notices) == 1
    assert A in (notices[0]["mentions"] or "")
    assert "provider_resolver" in notices[0]["content"]
    assert "sk-live-DEADBEEF0123456789" not in notices[0]["content"]


@pytest.mark.asyncio
async def test_a_dropped_user_message_gets_no_sender_notice(db_client, monkeypatch):
    """The sender is a person (usr_ prefix): there is no agent turn to wake,
    and the owner inbox notice is the whole remedy."""
    _patch_db(monkeypatch, db_client)
    await _agent(db_client, B)
    bus = LocalMessageBus(backend=db_client._backend)
    trigger = MessageBusTrigger(bus=bus)
    monkeypatch.setattr(trigger, "_invoke_runtime", _boom("disk full"))
    msg = BusMessage(message_id="m_user", channel_id=DM, from_agent=f"usr_{OWNER}", content="hi")

    for _ in range(3):
        await trigger._handle_channel_batch(B, DM, [msg], msg, channel_owner=f"usr_{OWNER}")

    assert await db_client.get("bus_messages", {"channel_id": DM, "msg_type": DELIVERY_FAILED_MSG_TYPE}) == []
    assert await BusDeliveryReceiptRepository(db_client).get("m_user", B) is None


@pytest.mark.asyncio
async def test_a_team_turn_writes_no_receipts(db_client, monkeypatch):
    _patch_db(monkeypatch, db_client)
    await _agent(db_client, B)
    bus = LocalMessageBus(backend=db_client._backend)
    trigger = MessageBusTrigger(bus=bus)
    _returns(monkeypatch, trigger, TurnResult(text="", event_id="evt", delivered=True))
    msg = BusMessage(message_id="m_team", channel_id="ch_room", from_agent=A, content="@B hi")

    await trigger._handle_channel_batch(
        B, "ch_room", [msg], msg, channel_owner=f"{TEAM_ROOM_OWNER_PREFIX}team_1"
    )

    assert await BusDeliveryReceiptRepository(db_client).get("m_team", B) is None


# ── 2026-09-09 (review C2): the drop wake is windowed, never a loop ─────────


async def _drop(trigger, db_client, tools, text):
    msg = await _seed_dm_text(db_client, tools, text)
    for _ in range(3):
        await trigger._handle_channel_batch(B, msg.channel_id, [msg], msg, channel_owner=A)
    return msg


async def _seed_dm_text(db_client, tools, text):
    out = await tools["message_agent"](agent_id=A, to=B, text=text)
    row = await db_client.get_one("bus_messages", {"message_id": out["message_id"]})
    return BusMessage(
        message_id=row["message_id"], channel_id=row["channel_id"],
        from_agent=A, content=row["content"], created_at=row["created_at"],
    )


async def _failed_notices(db_client, channel_id):
    return await db_client.get(
        "bus_messages", {"channel_id": channel_id, "msg_type": DELIVERY_FAILED_MSG_TYPE}
    )


@pytest.mark.asyncio
async def test_repeated_drops_wake_the_sender_once_per_window(db_client, monkeypatch):
    """A recipient that stays broken must not turn the drop notice into a
    ping-pong: notice wakes A, A rephrases and resends, B drops again, notice
    wakes A... One wake per (recipient, channel) per window; the window then
    expires so a recipient that is fixed and breaks again is reported anew."""
    from datetime import timedelta

    from narranexus.platform.message_bus.message_bus_trigger import (
        FAILURE_NOTIFY_COOLDOWN_SECONDS,
    )
    from narranexus.platform.repository.owner_notice_cooldown_repository import (
        OwnerNoticeCooldownRepository,
    )
    from narranexus.platform.utils.timezone import utc_now

    _patch_db(monkeypatch, db_client)
    await _agent(db_client, A)
    await _agent(db_client, B)
    tools, bus = _tools(db_client)
    trigger = MessageBusTrigger(bus=bus)
    monkeypatch.setattr(trigger, "_invoke_runtime", _boom("worker crashed"))

    first = await _drop(trigger, db_client, tools, "build the site")
    await _drop(trigger, db_client, tools, "please build the site now")   # rephrased
    await _drop(trigger, db_client, tools, "build the site")               # verbatim
    notices = await _failed_notices(db_client, first.channel_id)
    assert len(notices) == 1, "one wake per window, whatever the wording"

    # The window elapses (B was fixed, then broke again next day).
    await OwnerNoticeCooldownRepository(db_client).arm(
        B, first.channel_id, "peer_drop",
        at=utc_now() - timedelta(seconds=FAILURE_NOTIFY_COOLDOWN_SECONDS + 5),
    )
    await _drop(trigger, db_client, tools, "a brand new request")
    assert len(await _failed_notices(db_client, first.channel_id)) == 2


@pytest.mark.asyncio
async def test_the_same_content_dropped_again_is_recognised_by_fingerprint(db_client, monkeypatch):
    """Second layer, independent of the cooldown row: the receipt ledger
    already holds a `dropped` row with this content fingerprint in this
    channel, so even with no cooldown row the sender is not woken again."""
    _patch_db(monkeypatch, db_client)
    await _agent(db_client, A)
    await _agent(db_client, B)
    tools, bus = _tools(db_client)
    trigger = MessageBusTrigger(bus=bus)
    monkeypatch.setattr(trigger, "_invoke_runtime", _boom("worker crashed"))

    first = await _drop(trigger, db_client, tools, "build the site")
    await db_client.delete("owner_notice_cooldowns", {"agent_id": B})
    await _drop(trigger, db_client, tools, "build  the site")
    assert len(await _failed_notices(db_client, first.channel_id)) == 1
    # Different content with no cooldown row → a fresh wake (the positive case).
    await db_client.delete("owner_notice_cooldowns", {"agent_id": B})
    await _drop(trigger, db_client, tools, "something unrelated")
    assert len(await _failed_notices(db_client, first.channel_id)) == 2


# ── 2026-09-09 (review C1): the pre-flight is read-only ─────────────────────


@pytest.mark.asyncio
async def test_pre_flight_never_calls_the_turn_gate(db_client, monkeypatch):
    """`should_skip` is the TURN gate and is allowed side effects (claiming a
    half-open probe). The send-side receipt must use the read-only
    `peek_skip`, or every DM to a paused recipient would consume the one
    probe that lets it recover."""
    from narranexus.platform.agent_framework.loop import circuit_breaker as cb

    _patch_db(monkeypatch, db_client)
    await _agent(db_client, A)
    await _agent(db_client, B)
    await db_client.insert(
        "instance_agent_circuit_breaker",
        {"agent_id": B, "cb_status": "probing", "paused_reason": "auth"},
    )

    async def _forbidden(*_a, **_k):
        raise AssertionError("should_skip must not be called from the send tool")

    monkeypatch.setattr(cb, "should_skip", _forbidden)
    tools, _ = _tools(db_client)

    out = await tools["message_agent"](agent_id=A, to=B, text="hi")

    assert out["success"] is True
    # An unknown/future status is held, never silently accepted.
    assert out["receipt"]["status"] == RECEIPT_HELD
    assert "probing" in out["receipt"]["reason"]
