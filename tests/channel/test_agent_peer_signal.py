"""
@file_name: test_agent_peer_signal.py
@author:
@date: 2026-08-26
@description: "Is the far side another agent?" — one definition, filled
everywhere, reaching the model.

The DM Communication Protocol's "Breaking a Loop" section leans on knowing
whether the other party is a machine, and until now the platform had no
way to say so: the model could only guess from how the messages read.

This pins three things, because the signal is worthless if any one of them
is missing:
  1. the seam answers (per channel, one definition)
  2. every ChannelTag construction site fills it
  3. the tag actually renders it, so the model sees it

Point 2 is the fragile one. ``build_trigger_extra_data`` was once
hand-rolled at four sites and a new key was added to exactly one of them —
the same shape as the tag sites here.
"""
from __future__ import annotations

import inspect

import pytest

from xyz_agent_context.channel.channel_trigger_base import ChannelTriggerBase
from xyz_agent_context.module.channel_trigger_map import CHANNEL_TRIGGER_MAP
from xyz_agent_context.schema.channel_tag import AGENT_PEER_MARKER, ChannelTag
from xyz_agent_context.schema.parsed_message import ChatType, ParsedMessage


def _msg(sender_id: str) -> ParsedMessage:
    return ParsedMessage(
        message_id="m1",
        chat_id="!room",
        sender_id=sender_id,
        sender_name="somebody",
        content="hi",
        chat_type=ChatType.PRIVATE,
    )


# ── 1. the seam ───────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "cls", sorted(CHANNEL_TRIGGER_MAP.values(), key=lambda c: c.__name__)
)
def test_every_channel_answers_the_question(cls):
    assert callable(getattr(cls, "is_agent_peer", None))


def test_the_default_is_human():
    """Guessing "human" is the safe direction: it changes nothing about how
    the turn is handled, it only withholds one hint from the model."""
    assert ChannelTriggerBase.is_agent_peer(None, _msg("U123")) is False  # type: ignore[arg-type]


def test_narramessenger_reads_it_off_the_mxid():
    """The platform mints agent identities as ``@agent-<id>:<homeserver>``,
    so on this channel it is a fact rather than a guess — and this is the
    one channel where two agents routinely hold a 1:1 conversation with
    nobody else in the room."""
    matrix = CHANNEL_TRIGGER_MAP["narramessenger"]
    assert matrix.is_agent_peer(None, _msg("@agent-e7726996:matrix.netmind.chat")) is True  # type: ignore[arg-type]
    assert matrix.is_agent_peer(None, _msg("@liam:matrix.netmind.chat")) is False  # type: ignore[arg-type]


# ── 2. every construction site fills it ───────────────────────────────

def _code(func) -> str:
    lines = inspect.getsource(func).splitlines()
    return "\n".join(ln for ln in lines if not ln.strip().startswith("#"))


def test_every_channel_tag_built_on_the_receive_path_fills_the_flag():
    """A site that forgets it does not fail — it silently reports "human",
    which is exactly how a signal like this rots. ``build_trigger_extra_data``
    already taught this lesson: hand-rolled at four sites, new key added to
    one.
    """
    import xyz_agent_context.channel.channel_trigger_base as base
    import xyz_agent_context.module.lark_module.lark_trigger as lark
    import xyz_agent_context.module.narramessenger_module.matrix_trigger as matrix

    missing = []
    for mod in (base, lark, matrix):
        src = _code(mod)
        # Every ChannelTag(...) / ChannelTag.lark(...) on these modules is a
        # receive-path tag; count them against the fills.
        built = src.count("ChannelTag(") + src.count("ChannelTag.lark(")
        filled = src.count("is_agent_peer=self.is_agent_peer(")
        if built != filled:
            missing.append(f"{mod.__name__}: {built} built, {filled} filled")
    assert not missing, (
        f"ChannelTag sites that do not fill is_agent_peer: {missing}. A "
        f"missing fill reads as 'human' — silently."
    )


def test_managed_mode_stamps_the_flag_too():
    """Managed turns never run a context builder, so the native fill cannot
    reach them; without its own stamp every managed A2A DM reads as a human
    conversation."""
    from xyz_agent_context.module.managed_channel_ingress import (
        ManagedChannelIngress,
    )

    assert "is_agent_peer" in _code(ManagedChannelIngress.before_run)


# ── 3. the model actually sees it ─────────────────────────────────────

def test_an_agent_sender_is_marked_in_the_rendered_tag():
    tag = ChannelTag(
        channel="narramessenger", sender_name="Liam",
        sender_id="@agent-x:h", room_id="!room", is_agent_peer=True,
    )
    assert AGENT_PEER_MARKER in tag.format()


def test_a_human_tag_is_byte_identical_to_before():
    """These strings land in chat history; changing the shape for every
    turn would make old and new turns disagree."""
    tag = ChannelTag(
        channel="lark", sender_name="Alice", sender_id="ou_1", room_id="oc_1",
    )
    assert tag.format() == "[Lark · Alice · ou_1 · oc_1]"


def test_the_marker_survives_a_round_trip():
    tag = ChannelTag(
        channel="narramessenger", sender_name="Liam",
        sender_id="@agent-x:h", room_id="!room", is_agent_peer=True,
    )
    back = ChannelTag.parse(tag.format())
    assert back is not None
    assert back.is_agent_peer is True
    assert back.room_id == "!room"


def test_a_room_less_agent_tag_does_not_parse_the_marker_as_a_room():
    tag = ChannelTag(
        channel="wechat", sender_name="Bot", sender_id="wx1", is_agent_peer=True,
    )
    back = ChannelTag.parse(tag.format())
    assert back is not None
    assert back.room_id == "", "the marker must not be read as a room id"
    assert back.is_agent_peer is True


def test_the_flag_is_dropped_from_the_wire_when_false():
    """``to_dict`` strips falsy fields, so existing serialised tags are
    unchanged by this PR."""
    assert "is_agent_peer" not in ChannelTag(
        channel="lark", sender_name="A", sender_id="ou_1"
    ).to_dict()
    assert ChannelTag(
        channel="lark", sender_name="A", sender_id="ou_1", is_agent_peer=True
    ).to_dict()["is_agent_peer"] is True


def test_the_prompt_clause_names_the_marker():
    """The protocol text and the marker must agree — a clause naming a
    marker the tag never renders is a branch the model cannot take."""
    from xyz_agent_context.channel.channel_prompts import (
        COMMUNICATION_PROTOCOL_DIRECT,
    )

    assert AGENT_PEER_MARKER in COMMUNICATION_PROTOCOL_DIRECT
