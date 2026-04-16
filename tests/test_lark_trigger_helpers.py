"""Tests for LarkTrigger helper methods — reply detection, event parsing, dedup."""

import json

import pytest

from xyz_agent_context.module.lark_module.lark_trigger import LarkTrigger


# =====================================================================
# _extract_lark_reply
# =====================================================================

class TestExtractLarkReply:
    """Test V1 and V2 reply detection from tool call items."""

    def test_v1_text(self):
        item = {
            "type": "tool_call_item",
            "tool_name": "lark_send_message",
            "arguments": {"text": "Hello!", "chat_id": "oc_123"},
        }
        assert LarkTrigger._extract_lark_reply(item) == "Hello!"

    def test_v1_markdown(self):
        item = {
            "type": "tool_call_item",
            "tool_name": "lark_send_message",
            "arguments": {"markdown": "**Bold**", "chat_id": "oc_123"},
        }
        assert LarkTrigger._extract_lark_reply(item) == "**Bold**"

    def test_v1_json_string_args(self):
        item = {
            "type": "tool_call_item",
            "tool_name": "lark_send_message",
            "arguments": json.dumps({"text": "from json", "chat_id": "oc_123"}),
        }
        assert LarkTrigger._extract_lark_reply(item) == "from json"

    def test_v2_messages_send(self):
        item = {
            "type": "tool_call_item",
            "tool_name": "lark_cli",
            "arguments": {
                "agent_id": "agent_1",
                "command": 'im +messages-send --chat-id oc_123 --text "Hi there"',
            },
        }
        assert LarkTrigger._extract_lark_reply(item) == "Hi there"

    def test_v2_messages_reply(self):
        item = {
            "type": "tool_call_item",
            "tool_name": "lark_cli",
            "arguments": {
                "agent_id": "agent_1",
                "command": 'im +messages-reply --message-id om_123 --text "Reply text"',
            },
        }
        assert LarkTrigger._extract_lark_reply(item) == "Reply text"

    def test_v2_json_string_args(self):
        item = {
            "type": "tool_call_item",
            "tool_name": "lark_cli",
            "arguments": json.dumps({
                "agent_id": "agent_1",
                "command": 'im +messages-send --chat-id oc_123 --text "parsed"',
            }),
        }
        assert LarkTrigger._extract_lark_reply(item) == "parsed"

    def test_v2_non_messaging_command(self):
        """Non-messaging lark_cli commands should return empty string."""
        item = {
            "type": "tool_call_item",
            "tool_name": "lark_cli",
            "arguments": {
                "agent_id": "agent_1",
                "command": "contact +search-user --query John",
            },
        }
        assert LarkTrigger._extract_lark_reply(item) == ""

    def test_v2_fallback_no_text_flag(self):
        """Send command without --text should return sentinel."""
        item = {
            "type": "tool_call_item",
            "tool_name": "lark_cli",
            "arguments": {
                "agent_id": "agent_1",
                "command": "im +messages-send --chat-id oc_123",
            },
        }
        assert LarkTrigger._extract_lark_reply(item) == "(sent via lark_cli)"

    def test_unrelated_tool(self):
        item = {
            "type": "tool_call_item",
            "tool_name": "search_documents",
            "arguments": {"query": "meeting notes"},
        }
        assert LarkTrigger._extract_lark_reply(item) == ""

    def test_empty_args(self):
        item = {
            "type": "tool_call_item",
            "tool_name": "lark_cli",
            "arguments": {},
        }
        assert LarkTrigger._extract_lark_reply(item) == ""

    def test_invalid_json_args(self):
        item = {
            "type": "tool_call_item",
            "tool_name": "lark_send_message",
            "arguments": "not valid json {{{",
        }
        assert LarkTrigger._extract_lark_reply(item) == ""


# =====================================================================
# _sdk_event_to_dict
# =====================================================================

class TestSdkEventToDict:
    def test_returns_empty_on_bad_input(self):
        assert LarkTrigger._sdk_event_to_dict(None) == {}
        assert LarkTrigger._sdk_event_to_dict("not an event") == {}

    def test_returns_empty_on_missing_attrs(self):
        class FakeData:
            pass
        assert LarkTrigger._sdk_event_to_dict(FakeData()) == {}


# =====================================================================
# _parse_event_fields
# =====================================================================

class TestParseEventFields:
    def test_compact_format(self):
        event = {
            "chat_id": "oc_123",
            "sender_id": "ou_456",
            "sender_name": "John",
            "content": '{"text": "hello"}',
            "message_id": "om_789",
        }
        fields = LarkTrigger._parse_event_fields(event)
        assert fields["chat_id"] == "oc_123"
        assert fields["sender_id"] == "ou_456"
        assert fields["sender_name"] == "John"
        assert fields["message_id"] == "om_789"

    def test_defaults(self):
        fields = LarkTrigger._parse_event_fields({})
        assert fields["chat_id"] == ""
        assert fields["sender_id"] == ""
        assert fields["sender_name"] == "Unknown"
        assert fields["message_id"] == ""


# =====================================================================
# _parse_content
# =====================================================================

class TestParseContent:
    def test_plain_text(self):
        assert LarkTrigger._parse_content("hello") == "hello"

    def test_json_text(self):
        assert LarkTrigger._parse_content('{"text": "hello"}') == "hello"

    def test_invalid_json(self):
        assert LarkTrigger._parse_content("{invalid json") == "{invalid json"

    def test_whitespace(self):
        assert LarkTrigger._parse_content("  hello  ") == "hello"


# =====================================================================
# _sanitize_display_name
# =====================================================================

class TestSanitizeDisplayName:
    def test_normal(self):
        assert LarkTrigger._sanitize_display_name("John Doe") == "John Doe"

    def test_none(self):
        assert LarkTrigger._sanitize_display_name(None) == "Unknown"

    def test_empty(self):
        assert LarkTrigger._sanitize_display_name("") == "Unknown"

    def test_truncate(self):
        long_name = "A" * 200
        result = LarkTrigger._sanitize_display_name(long_name)
        assert len(result) == 128
