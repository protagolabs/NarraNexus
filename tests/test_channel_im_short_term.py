"""
@file_name: test_channel_im_short_term.py
@author: NetMind.AI
@date: 2026-06-24
@description: T4 + T9 — IM short-term memory wiring in ChannelTriggerBase: the
distrust prompt block formatter, and the load/write round-trip used by a distrust
turn (isolated per room).
"""
import pytest

from xyz_agent_context.channel.channel_trigger_base import (
    ChannelTriggerBase,
    _format_im_short_term_block,
)
from xyz_agent_context.schema.parsed_message import ParsedMessage


# ---- pure formatter -------------------------------------------------------

def test_format_block_empty_when_no_rows():
    assert _format_im_short_term_block([]) == ""


def test_format_block_labels_each_line_and_orders():
    rows = [
        {"sender": "u1", "role": "user", "body": "hi"},
        {"sender": "agent", "role": "agent", "body": "hello"},
    ]
    out = _format_im_short_term_block(rows)
    assert out.startswith("[Recent messages")
    assert "u1: hi" in out
    assert "agent: hello" in out
    assert out.index("u1: hi") < out.index("agent: hello")  # chronological


def test_format_block_falls_back_to_role_when_no_sender():
    out = _format_im_short_term_block([{"sender": None, "role": "agent", "body": "x"}])
    assert "agent: x" in out


# ---- load / write round-trip ---------------------------------------------

class _FakeTrigger(ChannelTriggerBase):
    channel_name = "faketest"
    brand_display = "Fake"

    async def connect(self, credential):  # pragma: no cover - not exercised
        yield

    def parse_event(self, raw):
        return None

    async def is_echo(self, message, credential):
        return False

    async def resolve_sender_name(self, sender_id, credential):
        return "x"

    def create_context_builder(self, message, credential, agent_id):
        return None

    async def load_active_credentials(self):
        return []


def _trigger(db):
    # Bypass the heavy __init__ (workers, pollers) — these unit tests only touch
    # _db and the two short-term helpers.
    t = _FakeTrigger.__new__(_FakeTrigger)
    t._db = db
    return t


def _msg(chat_id, sender_id, content, mid):
    return ParsedMessage(
        message_id=mid, chat_id=chat_id, sender_id=sender_id, content=content
    )


@pytest.mark.asyncio
async def test_write_then_load_roundtrip(db_client):
    t = _trigger(db_client)
    await t._write_im_short_term(
        "agent_x", "owner_x", _msg("room1", "u1", "hello bot", "m1"), "hi there"
    )
    block = await t._load_im_short_term_block("agent_x", "room1")
    assert "u1: hello bot" in block
    assert "agent: hi there" in block


@pytest.mark.asyncio
async def test_load_isolated_by_room(db_client):
    t = _trigger(db_client)
    await t._write_im_short_term("agent_x", "o", _msg("roomA", "a", "secretA", "1"), "rA")
    await t._write_im_short_term("agent_x", "o", _msg("roomB", "b", "secretB", "2"), "rB")

    block_a = await t._load_im_short_term_block("agent_x", "roomA")
    assert "secretA" in block_a
    assert "secretB" not in block_a


@pytest.mark.asyncio
async def test_load_empty_without_db():
    t = _FakeTrigger.__new__(_FakeTrigger)
    t._db = None
    assert await t._load_im_short_term_block("agent_x", "room1") == ""
