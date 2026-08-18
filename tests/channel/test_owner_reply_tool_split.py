"""
@file_name: test_owner_reply_tool_split.py
@author:
@date: 2026-08-17
@description: `send_message_to_user_directly` became two names — the accounting
must recognise both, on the surfaces that use them.

The split (decision: 2026-08-17) exists because the one tool carried two
OPPOSITE disciplines: answering your owner is expected on almost every chat
turn, notifying your owner from someone else's conversation is something you do
only for (a)/(b)/(c). Each name now carries its own.

The hazard the split introduces is here: `_has_organic_reply` asks the registry
"did this turn speak?", and a registry that knows only one of the two names goes
blind on the surface that uses the other. On the owner's chat turn that means a
perfectly answered turn reads as "never spoke", and the helper-LLM fallback
writes a SECOND reply on top of it — every time.
"""
from __future__ import annotations

from xyz_agent_context.channel.message_source_handler import MessageSourceRegistry


def test_an_owner_chat_turn_counts_reply_owner_as_speaking():
    """The owner's chat turn has `reply_owner` on its desk and nothing else."""
    handler = MessageSourceRegistry.get("chat")
    assert handler.is_user_reply_tool("mcp__chat_module__reply_owner")


def test_every_other_surface_counts_notify_owner_as_owner_visible():
    """`notify_owner` is the one tool that means "put this in the owner's
    window", and it is on every non-chat desk."""
    for source in ("lark", "slack", "telegram", "wechat", "message_bus", "job"):
        handler = MessageSourceRegistry.get(source)
        assert handler.is_owner_visible_reply_tool(
            f"mcp__chat_module__notify_owner"
        ), f"{source} does not recognise notify_owner as owner-visible"


def test_the_bus_still_separates_delivered_from_owner_visible():
    """A peer reply reaches the peer, not the owner's window. Conflating the two
    let every agent-to-agent reply re-anchor the owner's session (PR #230)."""
    handler = MessageSourceRegistry.get("message_bus")
    assert handler.is_user_reply_tool("mcp__message_bus_module__message_agent")
    assert not handler.is_owner_visible_reply_tool(
        "mcp__message_bus_module__message_agent"
    )


def test_the_retired_name_is_gone_from_every_registration():
    """`send_message_to_user_directly` named a mechanism and misdescribed its
    scope — on an IM turn the "user" the agent faces is the IM sender, while the
    tool wrote to the owner. Two prompt sections existed only to correct that."""
    for source in ("chat", "lark", "slack", "telegram", "wechat",
                   "message_bus", "job", "discord", "narramessenger"):
        handler = MessageSourceRegistry.get(source)
        joined = " ".join(handler.user_reply_tool_names)
        assert "send_message_to_user_directly" not in joined, source
