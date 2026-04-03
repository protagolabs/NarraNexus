"""
@file_name: test_local_bus.py
@author: NarraNexus
@date: 2026-04-02
@description: Tests for LocalMessageBus implementation

Comprehensive test suite covering messaging, channel management,
agent discovery, and the cursor-based delivery model including
poison message filtering.
"""

from __future__ import annotations

import pytest

from xyz_agent_context.message_bus.local_bus import LocalMessageBus
from xyz_agent_context.utils.db_backend_sqlite import SQLiteBackend
from xyz_agent_context.utils.database_table_management.create_message_bus_tables import (
    create_bus_tables_sqlite,
)


@pytest.fixture
async def bus():
    """Provide a LocalMessageBus backed by an in-memory SQLite database."""
    backend = SQLiteBackend(":memory:")
    await backend.initialize()
    await create_bus_tables_sqlite(backend)
    local_bus = LocalMessageBus(backend)
    yield local_bus
    await backend.close()


# --- Messaging ---


class TestSendMessage:
    """Tests for send_message."""

    async def test_returns_message_id_with_prefix(self, bus):
        """send_message should return an ID starting with 'msg_'."""
        ch_id = await bus.create_channel("test", ["agt_alice", "agt_bob"])
        msg_id = await bus.send_message("agt_alice", ch_id, "Hello")
        assert msg_id.startswith("msg_")

    async def test_message_persisted(self, bus):
        """Sent message should be retrievable via get_messages."""
        ch_id = await bus.create_channel("test", ["agt_alice"])
        await bus.send_message("agt_alice", ch_id, "Hello world")
        messages = await bus.get_messages(ch_id)
        assert len(messages) == 1
        assert messages[0].content == "Hello world"
        assert messages[0].from_agent == "agt_alice"


class TestGetMessages:
    """Tests for get_messages."""

    async def test_returns_sent_messages(self, bus):
        """get_messages should return all messages in a channel."""
        ch_id = await bus.create_channel("test", ["agt_alice", "agt_bob"])
        await bus.send_message("agt_alice", ch_id, "msg1")
        await bus.send_message("agt_bob", ch_id, "msg2")
        await bus.send_message("agt_alice", ch_id, "msg3")

        messages = await bus.get_messages(ch_id)
        assert len(messages) == 3
        assert [m.content for m in messages] == ["msg1", "msg2", "msg3"]

    async def test_since_filter(self, bus):
        """get_messages with since should only return newer messages."""
        ch_id = await bus.create_channel("test", ["agt_alice"])
        # Manually insert messages with known timestamps
        await bus._db.insert("bus_messages", {
            "message_id": "msg_old",
            "channel_id": ch_id,
            "from_agent": "agt_alice",
            "content": "old",
            "msg_type": "text",
            "created_at": "2026-04-01T10:00:00",
        })
        await bus._db.insert("bus_messages", {
            "message_id": "msg_new",
            "channel_id": ch_id,
            "from_agent": "agt_alice",
            "content": "new",
            "msg_type": "text",
            "created_at": "2026-04-02T10:00:00",
        })

        messages = await bus.get_messages(ch_id, since="2026-04-01T12:00:00")
        assert len(messages) == 1
        assert messages[0].content == "new"


# --- Unread ---


class TestGetUnread:
    """Tests for get_unread."""

    async def test_returns_unread_messages(self, bus):
        """get_unread should return messages after last_read_at."""
        ch_id = await bus.create_channel("test", ["agt_alice", "agt_bob"])

        # Send a message from alice (bob's last_read_at is set to channel creation time)
        await bus.send_message("agt_alice", ch_id, "Hello Bob")

        unread = await bus.get_unread("agt_bob")
        assert len(unread) == 1
        assert unread[0].content == "Hello Bob"


class TestMarkRead:
    """Tests for mark_read."""

    async def test_advances_read_cursor(self, bus):
        """After mark_read, those messages should no longer be unread."""
        ch_id = await bus.create_channel("test", ["agt_alice", "agt_bob"])

        msg_id = await bus.send_message("agt_alice", ch_id, "Hello Bob")

        # Verify unread before marking
        unread = await bus.get_unread("agt_bob")
        assert len(unread) == 1

        # Mark as read
        await bus.mark_read("agt_bob", [msg_id])

        # Verify no longer unread
        unread = await bus.get_unread("agt_bob")
        assert len(unread) == 0


# --- Channel Management ---


class TestCreateChannel:
    """Tests for create_channel."""

    async def test_returns_channel_id_with_prefix(self, bus):
        """create_channel should return an ID starting with 'ch_'."""
        ch_id = await bus.create_channel("General", ["agt_alice"])
        assert ch_id.startswith("ch_")

    async def test_adds_all_members(self, bus):
        """All specified members should be added to the channel."""
        members = ["agt_alice", "agt_bob", "agt_charlie"]
        ch_id = await bus.create_channel("Team", members)

        rows = await bus._db.get("bus_channel_members", {"channel_id": ch_id})
        member_ids = {row["agent_id"] for row in rows}
        assert member_ids == set(members)


class TestJoinLeaveChannel:
    """Tests for join_channel and leave_channel."""

    async def test_join_channel(self, bus):
        """join_channel should add a new member to the channel."""
        ch_id = await bus.create_channel("test", ["agt_alice"])

        await bus.join_channel("agt_bob", ch_id)

        rows = await bus._db.get("bus_channel_members", {"channel_id": ch_id})
        member_ids = {row["agent_id"] for row in rows}
        assert "agt_bob" in member_ids

    async def test_leave_channel(self, bus):
        """leave_channel should remove a member from the channel."""
        ch_id = await bus.create_channel("test", ["agt_alice", "agt_bob"])

        await bus.leave_channel("agt_bob", ch_id)

        rows = await bus._db.get("bus_channel_members", {"channel_id": ch_id})
        member_ids = {row["agent_id"] for row in rows}
        assert "agt_bob" not in member_ids
        assert "agt_alice" in member_ids


# --- Delivery (Cursor Model) ---


class TestGetPendingMessages:
    """Tests for get_pending_messages (core delivery model)."""

    async def test_returns_unprocessed_messages(self, bus):
        """get_pending_messages should return messages not yet processed."""
        ch_id = await bus.create_channel("test", ["agt_alice", "agt_bob"])
        await bus.send_message("agt_alice", ch_id, "Process me")

        pending = await bus.get_pending_messages("agt_bob")
        assert len(pending) == 1
        assert pending[0].content == "Process me"

    async def test_skips_self_sent_messages(self, bus):
        """get_pending_messages should not return messages sent by the agent itself."""
        ch_id = await bus.create_channel("test", ["agt_alice", "agt_bob"])
        await bus.send_message("agt_bob", ch_id, "My own message")

        pending = await bus.get_pending_messages("agt_bob")
        assert len(pending) == 0

    async def test_multiple_channels(self, bus):
        """get_pending_messages should aggregate across all channels."""
        ch1 = await bus.create_channel("ch1", ["agt_alice", "agt_bob"])
        ch2 = await bus.create_channel("ch2", ["agt_charlie", "agt_bob"])

        await bus.send_message("agt_alice", ch1, "From ch1")
        await bus.send_message("agt_charlie", ch2, "From ch2")

        pending = await bus.get_pending_messages("agt_bob")
        assert len(pending) == 2
        contents = {m.content for m in pending}
        assert contents == {"From ch1", "From ch2"}


class TestAckProcessed:
    """Tests for ack_processed."""

    async def test_advances_processed_cursor(self, bus):
        """After ack_processed, those messages should no longer be pending."""
        ch_id = await bus.create_channel("test", ["agt_alice", "agt_bob"])
        await bus.send_message("agt_alice", ch_id, "Process me")

        pending = await bus.get_pending_messages("agt_bob")
        assert len(pending) == 1

        # Acknowledge processing
        await bus.ack_processed("agt_bob", ch_id, pending[0].created_at)

        # No more pending
        pending = await bus.get_pending_messages("agt_bob")
        assert len(pending) == 0


class TestPoisonMessage:
    """Tests for poison message filtering."""

    async def test_skipped_after_3_failures(self, bus):
        """Messages with >= 3 failures should be skipped by get_pending_messages."""
        ch_id = await bus.create_channel("test", ["agt_alice", "agt_bob"])
        msg_id = await bus.send_message("agt_alice", ch_id, "Poison pill")

        # Verify it shows up initially
        pending = await bus.get_pending_messages("agt_bob")
        assert len(pending) == 1

        # Record 3 failures
        await bus.record_failure(msg_id, "agt_bob", "Error 1")
        await bus.record_failure(msg_id, "agt_bob", "Error 2")
        await bus.record_failure(msg_id, "agt_bob", "Error 3")

        # Verify failure count
        count = await bus.get_failure_count(msg_id, "agt_bob")
        assert count == 3

        # Should now be skipped
        pending = await bus.get_pending_messages("agt_bob")
        assert len(pending) == 0

    async def test_failure_count_zero_for_unknown(self, bus):
        """get_failure_count should return 0 for unknown message/agent pairs."""
        count = await bus.get_failure_count("msg_nonexistent", "agt_nobody")
        assert count == 0


# --- Agent Discovery ---


class TestAgentDiscovery:
    """Tests for register_agent and search_agents."""

    async def test_register_and_search(self, bus):
        """Registered agents should be findable via search_agents."""
        await bus.register_agent(
            agent_id="agt_translator",
            owner_user_id="user_1",
            capabilities=["translate", "summarize"],
            description="A translation agent",
            visibility="public",
        )

        results = await bus.search_agents("translate")
        assert len(results) == 1
        assert results[0].agent_id == "agt_translator"
        assert "translate" in results[0].capabilities

    async def test_search_by_description(self, bus):
        """search_agents should also match on description."""
        await bus.register_agent(
            agent_id="agt_helper",
            owner_user_id="user_1",
            capabilities=["chat"],
            description="Helpful customer service bot",
        )

        results = await bus.search_agents("customer")
        assert len(results) == 1
        assert results[0].agent_id == "agt_helper"

    async def test_search_no_results(self, bus):
        """search_agents should return empty list when nothing matches."""
        results = await bus.search_agents("nonexistent_capability")
        assert results == []

    async def test_register_upsert(self, bus):
        """Registering the same agent_id twice should update, not duplicate."""
        await bus.register_agent(
            agent_id="agt_evolving",
            owner_user_id="user_1",
            capabilities=["v1"],
            description="Version 1",
        )
        await bus.register_agent(
            agent_id="agt_evolving",
            owner_user_id="user_1",
            capabilities=["v2", "v3"],
            description="Version 2",
        )

        results = await bus.search_agents("v2")
        assert len(results) == 1
        assert results[0].capabilities == ["v2", "v3"]
        assert results[0].description == "Version 2"
