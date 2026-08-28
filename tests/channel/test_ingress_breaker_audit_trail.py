"""
@file_name: test_ingress_breaker_audit_trail.py
@author:
@date: 2026-08-28
@description: Binding rule #16 gate — a suppressed message leaves a row.

Once the breaker is wired, a message judged part of a storm is dropped and
the far side sees nothing: the assistant simply stops answering. That is
the 0802 symptom, and the 8/14 incident ran 70+ hours precisely because
"the bot went quiet" was not answerable from any durable record — the logs
had rotated and there was nothing in the DB to count.

So the rule for this path is: every drop writes a row, per message, and the
row carries enough to answer "why, and until when". The managed surface has
the same gate in `tests/backend/test_manyfold_im_ingress.py`; this file
covers the native one, which audits through a different call site — a fact
that only surfaced because a mutation removing the native audit left the
managed tests green.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.channel.channel_audit_events import (
    EVENT_INGRESS_BREAKER_TRIPPED,
    EVENT_INGRESS_DROPPED_BREAKER,
)
from xyz_agent_context.channel.channel_trigger_base import ChannelTriggerBase
from xyz_agent_context.repository.channel_trigger_audit_repository import (
    ChannelTriggerAuditRepository,
)
from xyz_agent_context.schema.parsed_message import ChatType, ParsedMessage
from xyz_agent_context.schema.hook_schema import WorkingSource

pytestmark = pytest.mark.asyncio


class _Cred:
    app_id = "app_1"
    agent_id = "agent_a"
    user_id = "user_owner"


class _StormTrigger(ChannelTriggerBase):
    """A real subclass, so the guard is built from its own class attributes
    and this also pins that the knobs are the ones the base declares."""

    channel_name = "fake_breaker"
    brand_display = "Fake"
    working_source = WorkingSource.LARK

    INGRESS_RATE_THRESHOLD = 5
    INGRESS_DUP_RATIO_THRESHOLD = 0.5

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.ran = 0

    async def load_active_credentials(self):  # pragma: no cover
        return []

    async def connect(self, credential):  # pragma: no cover
        yield {}

    def parse_event(self, raw):
        return ParsedMessage(
            message_id=raw["id"],
            chat_id="room_1",
            chat_type=ChatType.PRIVATE,
            sender_id="peer_1",
            sender_name="Peer",
            content=raw["content"],
            timestamp_ms=raw["ts_ms"],
        )

    async def is_echo(self, message, credential):
        return False

    async def resolve_sender_name(self, sender_id, credential):
        return sender_id

    def create_context_builder(self, message, credential, agent_id):  # pragma: no cover
        return None

    async def _build_and_run_agent(self, *a, **kw):
        """Short-circuited: this file is about what happens to the messages
        the breaker REFUSES. Letting the admitted ones build a real run
        would drag the whole context pipeline into a test whose subject is
        one audit row."""
        self.ran += 1
        return ""


async def _storm(db_client, n: int):
    """Feed n identical messages through the real `_process_message`."""
    trigger = _StormTrigger(base_workers=1)
    trigger._audit_repo = ChannelTriggerAuditRepository(
        _StormTrigger.channel_name, db_client
    )
    cred = _Cred()
    trigger._subscriber_creds[cred.app_id] = cred
    # `start()` is what builds the guard in production; this test drives
    # `_process_message` directly, so build it from the same factory rather
    # than hand-rolling one — otherwise the test would be pinning a guard
    # configured differently from the one that ships.
    trigger._ingress_guard = trigger._build_ingress_guard(db_client)
    for i in range(n):
        msg = trigger.parse_event(
            {"id": f"m{i}", "content": "same thing again", "ts_ms": 1000 + i}
        )
        await trigger._process_message(cred, msg)
    return trigger


async def _rows(db_client, event_type):
    return await db_client.get(
        "channel_trigger_audit",
        {"channel": _StormTrigger.channel_name, "event_type": event_type},
    )


async def test_every_message_the_breaker_drops_leaves_its_own_row(db_client):
    """Per message, not per trip.

    One row per trip would answer "it closed" but not "for how long, and
    how much did it swallow" — and a silent return answers neither, which
    is the blind spot that let the original incident run unnoticed.
    """
    await _storm(db_client, 12)

    trips = await _rows(db_client, EVENT_INGRESS_BREAKER_TRIPPED)
    drops = await _rows(db_client, EVENT_INGRESS_DROPPED_BREAKER)

    assert trips, "the moment the door closed is not in the DB"
    assert drops, "messages were suppressed with no trace at all"
    # Every message after the trip gets its own row, and each row names the
    # message it silenced — otherwise "which of my messages got through?"
    # is unanswerable.
    ids = [r["message_id"] for r in drops]
    assert len(ids) == len(set(ids)), f"rows are not per message: {ids}"


async def test_a_native_drop_row_says_why_and_until_when(db_client):
    import json

    await _storm(db_client, 12)
    drops = await _rows(db_client, EVENT_INGRESS_DROPPED_BREAKER)
    assert drops

    details = drops[0]["details"]
    if isinstance(details, str):
        details = json.loads(details)

    for field in (
        "session_key", "tier", "reason", "suppressed",
        "cooldown_remaining_seconds",
    ):
        assert field in details, f"{field} missing from {sorted(details)}"
    assert details["reason"] == "cooling"
    assert details["tier"] >= 1
    assert details["cooldown_remaining_seconds"] > 0, (
        "a row that cannot say how much longer leaves 'until when' "
        "unanswerable, which is half of what these rows are for"
    )
