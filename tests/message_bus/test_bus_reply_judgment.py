"""
@file_name: test_bus_reply_judgment.py
@date: 2026-08-04
@description: The message_bus source handler must count the bus delivery
tools as user replies.

2026-08-01 briefing-squad incident: agents that DID deliver via
``bus_send_message`` were still recorded NO-REPLY, because the handler's
``user_reply_tool_names`` only listed ``send_message_to_user_directly``.
Beyond mislabeling, this poisons any measurement of "how often do bus
agents actually fail to deliver" — the metric that decides whether a
platform-side delivery fallback is needed at all.
"""
from __future__ import annotations

import xyz_agent_context.message_bus  # noqa: F401 — triggers registration
from xyz_agent_context.channel.message_source_handler import MessageSourceRegistry


def _handler():
    return MessageSourceRegistry.get("message_bus")


def test_bus_send_message_counts_as_user_reply():
    assert _handler().is_user_reply_tool(
        "mcp__message_bus_module__bus_send_message"
    )


def test_bus_send_to_agent_counts_as_user_reply():
    assert _handler().is_user_reply_tool(
        "mcp__message_bus_module__bus_send_to_agent"
    )


def test_owner_relay_tool_still_counts():
    """Owner Relay turns legitimately reply via the owner-chat tool."""
    assert _handler().is_user_reply_tool(
        "mcp__chat_module__send_message_to_user_directly"
    )


def test_unrelated_bus_tool_does_not_count():
    """Reading history or discovering agents is not a delivery."""
    assert not _handler().is_user_reply_tool(
        "mcp__message_bus_module__bus_get_messages"
    )
