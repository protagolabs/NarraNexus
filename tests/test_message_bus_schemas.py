"""
@file_name: test_message_bus_schemas.py
@author: NarraNexus
@date: 2026-04-02
@description: Tests for MessageBus Pydantic schema models

Verifies:
1. BusMessage creation with all fields
2. BusChannel defaults (channel_type)
3. BusAgentInfo capabilities list default
"""

from __future__ import annotations

from xyz_agent_context.message_bus.schemas import (
    BusAgentInfo,
    BusChannel,
    BusChannelMember,
    BusMessage,
)


class TestBusMessage:
    """Tests for BusMessage schema."""

    def test_create_with_all_fields(self):
        """BusMessage should accept all fields including msg_type."""
        msg = BusMessage(
            message_id="msg_abc12345",
            channel_id="ch_def67890",
            from_agent="agt_alice",
            content="Hello world",
            msg_type="text",
            created_at="2026-04-02T10:00:00Z",
        )
        assert msg.message_id == "msg_abc12345"
        assert msg.channel_id == "ch_def67890"
        assert msg.from_agent == "agt_alice"
        assert msg.content == "Hello world"
        assert msg.msg_type == "text"
        assert msg.created_at == "2026-04-02T10:00:00Z"

    def test_msg_type_default(self):
        """msg_type should default to 'text'."""
        msg = BusMessage(
            message_id="msg_1",
            channel_id="ch_1",
            from_agent="agt_1",
            content="hi",
            created_at="2026-04-02T10:00:00Z",
        )
        assert msg.msg_type == "text"


class TestBusChannel:
    """Tests for BusChannel schema."""

    def test_channel_type_default(self):
        """channel_type should default to 'group'."""
        ch = BusChannel(
            channel_id="ch_001",
            name="General",
            created_by="agt_alice",
            created_at="2026-04-02T10:00:00Z",
        )
        assert ch.channel_type == "group"

    def test_direct_channel(self):
        """channel_type can be set to 'direct'."""
        ch = BusChannel(
            channel_id="ch_002",
            name="DM",
            channel_type="direct",
            created_by="agt_bob",
            created_at="2026-04-02T10:00:00Z",
        )
        assert ch.channel_type == "direct"


class TestBusChannelMember:
    """Tests for BusChannelMember schema."""

    def test_last_processed_at_default(self):
        """last_processed_at should default to None."""
        member = BusChannelMember(
            channel_id="ch_001",
            agent_id="agt_alice",
            joined_at="2026-04-02T10:00:00Z",
            last_read_at="2026-04-02T10:00:00Z",
        )
        assert member.last_processed_at is None


class TestBusAgentInfo:
    """Tests for BusAgentInfo schema."""

    def test_capabilities_default(self):
        """capabilities should default to an empty list."""
        agent = BusAgentInfo(
            agent_id="agt_test",
            owner_user_id="user_1",
            registered_at="2026-04-02T10:00:00Z",
            last_seen_at="2026-04-02T10:00:00Z",
        )
        assert agent.capabilities == []
        assert agent.description == ""
        assert agent.visibility == "private"

    def test_with_capabilities(self):
        """capabilities can be set to a list of strings."""
        agent = BusAgentInfo(
            agent_id="agt_smart",
            owner_user_id="user_2",
            capabilities=["chat", "translate", "summarize"],
            description="A smart assistant",
            visibility="public",
            registered_at="2026-04-02T10:00:00Z",
            last_seen_at="2026-04-02T10:00:00Z",
        )
        assert agent.capabilities == ["chat", "translate", "summarize"]
        assert agent.description == "A smart assistant"
        assert agent.visibility == "public"
