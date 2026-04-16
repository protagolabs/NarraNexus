"""Tests for LarkContextBuilder — message info, conversation history parsing."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from xyz_agent_context.module.lark_module.lark_context_builder import LarkContextBuilder
from xyz_agent_context.module.lark_module._lark_credential_manager import LarkCredential


def _make_credential(**overrides) -> LarkCredential:
    defaults = {
        "agent_id": "agent_test",
        "app_id": "cli_test123",
        "app_secret_ref": "ref",
        "brand": "lark",
        "profile_name": "agent_agent_test",
        "auth_status": "bot_ready",
    }
    defaults.update(overrides)
    return LarkCredential(**defaults)


def _make_event(**overrides) -> dict:
    defaults = {
        "chat_id": "oc_abc123",
        "chat_type": "p2p",
        "chat_name": "",
        "sender_id": "ou_sender1",
        "sender_name": "John",
        "content": "Hello bot",
        "message_id": "om_msg1",
        "create_time": "1713200000",
    }
    defaults.update(overrides)
    return defaults


class TestGetMessageInfo:
    @pytest.mark.asyncio
    async def test_basic_fields(self):
        cred = _make_credential()
        event = _make_event()
        cli = MagicMock()
        builder = LarkContextBuilder(event, cred, cli, "agent_test")

        info = await builder.get_message_info()

        assert info["agent_id"] == "agent_test"
        assert info["channel_display_name"] == "Lark"
        assert info["channel_key"] == "lark"
        assert info["room_id"] == "oc_abc123"
        assert info["sender_display_name"] == "John"
        assert info["sender_id"] == "ou_sender1"
        assert info["message_body"] == "Hello bot"

    @pytest.mark.asyncio
    async def test_send_tool_name_is_lark_cli(self):
        """V2: send_tool_name must be lark_cli, not lark_send_message."""
        cred = _make_credential()
        event = _make_event()
        cli = MagicMock()
        builder = LarkContextBuilder(event, cred, cli, "agent_test")

        info = await builder.get_message_info()

        assert info["send_tool_name"] == "lark_cli"

    @pytest.mark.asyncio
    async def test_reply_instruction_contains_chat_id(self):
        cred = _make_credential()
        event = _make_event(chat_id="oc_target")
        cli = MagicMock()
        builder = LarkContextBuilder(event, cred, cli, "agent_test")

        info = await builder.get_message_info()

        assert "reply_instruction" in info
        assert "oc_target" in info["reply_instruction"]
        assert "lark_cli" in info["reply_instruction"]

    @pytest.mark.asyncio
    async def test_feishu_brand_display(self):
        cred = _make_credential(brand="feishu")
        event = _make_event()
        cli = MagicMock()
        builder = LarkContextBuilder(event, cred, cli, "agent_test")

        info = await builder.get_message_info()
        assert info["channel_display_name"] == "Feishu"

    @pytest.mark.asyncio
    async def test_group_room_type(self):
        cred = _make_credential()
        event = _make_event(chat_type="group")
        cli = MagicMock()
        builder = LarkContextBuilder(event, cred, cli, "agent_test")

        info = await builder.get_message_info()
        assert info["room_type"] == "Group Room"


class TestGetConversationHistory:
    @pytest.mark.asyncio
    async def test_empty_chat_id(self):
        cred = _make_credential()
        event = _make_event(chat_id="")
        cli = MagicMock()
        builder = LarkContextBuilder(event, cred, cli, "agent_test")

        history = await builder.get_conversation_history(limit=10)
        assert history == []

    @pytest.mark.asyncio
    async def test_cli_failure(self):
        cred = _make_credential()
        event = _make_event()
        cli = MagicMock()
        cli.list_chat_messages = AsyncMock(return_value={"success": False, "error": "timeout"})
        builder = LarkContextBuilder(event, cred, cli, "agent_test")

        history = await builder.get_conversation_history(limit=10)
        assert history == []

    @pytest.mark.asyncio
    async def test_normalizes_fields(self):
        cred = _make_credential()
        event = _make_event()
        cli = MagicMock()
        cli.list_chat_messages = AsyncMock(return_value={
            "success": True,
            "data": {
                "data": {
                    "items": [
                        {
                            "create_time": "1713200000",
                            "sender_id": "ou_abc",
                            "content": '{"text": "hello"}',
                        },
                        {
                            "create_time": "1713200001",
                            "sender_id": "ou_def",
                            "content": "plain text",
                        },
                    ]
                }
            },
        })
        builder = LarkContextBuilder(event, cred, cli, "agent_test")

        history = await builder.get_conversation_history(limit=10)
        assert len(history) == 2
        assert history[0]["timestamp"] == "1713200000"
        assert history[0]["sender"] == "ou_abc"
        assert history[0]["body"] == "hello"  # JSON unwrapped
        assert history[1]["body"] == "plain text"

    @pytest.mark.asyncio
    async def test_handles_list_data(self):
        """CLI may return data as a flat list instead of nested dict."""
        cred = _make_credential()
        event = _make_event()
        cli = MagicMock()
        cli.list_chat_messages = AsyncMock(return_value={
            "success": True,
            "data": [
                {"create_time": "t1", "sender_id": "ou_1", "content": "msg1"},
            ],
        })
        builder = LarkContextBuilder(event, cred, cli, "agent_test")

        history = await builder.get_conversation_history(limit=10)
        assert len(history) == 1
        assert history[0]["body"] == "msg1"


class TestGetRoomMembers:
    @pytest.mark.asyncio
    async def test_returns_empty(self):
        cred = _make_credential()
        event = _make_event()
        cli = MagicMock()
        builder = LarkContextBuilder(event, cred, cli, "agent_test")

        members = await builder.get_room_members()
        assert members == []
