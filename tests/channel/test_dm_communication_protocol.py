"""
@file_name: test_dm_communication_protocol.py
@author:
@date: 2026-08-06
@description: Pins the DM-vs-group split of the IM Communication Protocol.

The shared channel prompt used to inject ONE Communication Protocol into
every channel turn regardless of room type: "Your default action is NO
REPLY", "When to Stay Silent (most of the time)", "When to Reply (rare)",
plus group-chat and @mention discipline. That rule set was tuned in
2026-03 for three GROUP problems (agent-to-agent ack loops, every group
message waking every member, @mention abuse) — applying it to a 1:1 DM
from a real person made silence the correct behaviour, which is the
0802 WeChat report: a person sends "hello" and the agent answers nothing.

WeChat is the extreme case: its context builder hard-codes room_type to
"Direct Message" (personal-account v1 is DM-only), so 100% of its turns
were being told not to reply.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.channel.channel_prompts import (
    CHANNEL_MESSAGE_EXECUTION_TEMPLATE,
    COMMUNICATION_PROTOCOL_DIRECT,
    COMMUNICATION_PROTOCOL_GROUP,
    ROOM_TYPE_DIRECT,
    ROOM_TYPE_GROUP,
    communication_protocol_for,
)

# Phrases that make silence the default. Legitimate in a group, corrosive
# in a 1:1 DM.
GROUP_ONLY_MARKERS = (
    "Your default action is NO REPLY.",
    "### When to Stay Silent (most of the time)",
    "### When to Reply (rare)",
    "### Group Chat Rules",
    "### @Mention Discipline",
    "Being @mentioned does NOT obligate you to reply.",
)

# Discipline that stays true in BOTH room types — brevity and no
# performative reporting are not group-specific.
SHARED_MARKERS = (
    "Be brief.",
    "No performative reporting.",
)


class TestRoomTypeConstants:
    """room_type was an accidental enum — six builders each spelled the
    two literals by hand. Constants make it a real one."""

    def test_constants_keep_the_historical_wire_values(self):
        # These strings are rendered into the prompt ("Conversation Type")
        # and are what every existing builder already emitted.
        assert ROOM_TYPE_DIRECT == "Direct Message"
        assert ROOM_TYPE_GROUP == "Group Room"


class TestProtocolSelection:
    def test_direct_message_gets_the_direct_protocol(self):
        assert communication_protocol_for(ROOM_TYPE_DIRECT) == COMMUNICATION_PROTOCOL_DIRECT

    def test_group_room_gets_the_group_protocol(self):
        assert communication_protocol_for(ROOM_TYPE_GROUP) == COMMUNICATION_PROTOCOL_GROUP

    @pytest.mark.parametrize("unknown", ["", None, "Thread", "direct message"])
    def test_unknown_room_type_falls_back_to_group(self, unknown):
        """Conservative default: an unrecognised room type keeps the
        stricter discipline. Being too quiet in an unknown room type is
        recoverable; spraying replies into a 500-person group is not."""
        assert communication_protocol_for(unknown) == COMMUNICATION_PROTOCOL_GROUP


class TestDirectProtocolContent:
    def test_direct_protocol_drops_every_silence_default(self):
        for marker in GROUP_ONLY_MARKERS:
            assert marker not in COMMUNICATION_PROTOCOL_DIRECT, (
                f"1:1 DM protocol must not carry group discipline: {marker!r}"
            )

    def test_direct_protocol_makes_replying_the_default(self):
        assert "Replying is the default." in COMMUNICATION_PROTOCOL_DIRECT

    def test_direct_protocol_keeps_a_narrow_silence_carve_out(self):
        """Owner decision (2026-08-06): keep a narrow carve-out rather
        than forcing a reply to every "谢谢" — but it must be scoped to
        pure acknowledgment with nothing to add."""
        assert "pure acknowledgment" in COMMUNICATION_PROTOCOL_DIRECT

    def test_direct_protocol_keeps_the_shared_style_rules(self):
        for marker in SHARED_MARKERS:
            assert marker in COMMUNICATION_PROTOCOL_DIRECT


class TestGroupProtocolUnchanged:
    def test_group_protocol_keeps_all_discipline(self):
        """The group path is the tuned 2026-03 rule set — this change must
        not weaken it."""
        for marker in GROUP_ONLY_MARKERS:
            assert marker in COMMUNICATION_PROTOCOL_GROUP
        for marker in SHARED_MARKERS:
            assert marker in COMMUNICATION_PROTOCOL_GROUP


class TestTemplateWiring:
    def test_template_takes_the_protocol_as_a_placeholder(self):
        assert "{communication_protocol}" in CHANNEL_MESSAGE_EXECUTION_TEMPLATE

    def test_template_no_longer_hardcodes_group_discipline(self):
        """If the protocol text stayed inline, the placeholder would be
        dead and every room type would keep getting group rules."""
        for marker in GROUP_ONLY_MARKERS:
            assert marker not in CHANNEL_MESSAGE_EXECUTION_TEMPLATE

    def test_template_keeps_the_reply_tool_contract(self):
        """Independent of room type: the sender only sees a reply that
        went through the channel reply tool. This is the other half of
        the 0802 bug and must survive the split."""
        assert "{reply_instruction}" in CHANNEL_MESSAGE_EXECUTION_TEMPLATE
        assert "This is the ONLY way the sender will see your response" in (
            CHANNEL_MESSAGE_EXECUTION_TEMPLATE
        )

    def test_template_keeps_the_three_delivery_routes(self):
        """Guards the Bug-23 path rules that
        tests/channel/test_channel_prompts_path_rules.py also pins —
        asserted here so a careless protocol split fails fast."""
        assert "## File & Path Rules for IM Delivery" in CHANNEL_MESSAGE_EXECUTION_TEMPLATE
