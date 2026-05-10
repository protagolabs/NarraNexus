"""
@file_name: telegram_context_builder.py
@date: 2026-05-09
@description: Build execution context for Telegram-triggered messages.

Inherits ChannelContextBuilderBase. Telegram has NO conversation history
API for bots (bots only see messages that arrive after they're added /
sent), so ``get_conversation_history`` always returns ``[]``. Agent
relies on its ChatModule memory.
"""

from __future__ import annotations

from typing import Any, Dict, List

from xyz_agent_context.channel.channel_context_builder_base import (
    ChannelContextBuilderBase,
)
from xyz_agent_context.schema.parsed_message import ParsedMessage

from ._telegram_credential_manager import TelegramCredential


class TelegramContextBuilder(ChannelContextBuilderBase):
    """Telegram-specific context builder."""

    def __init__(
        self,
        message: ParsedMessage,
        credential: TelegramCredential,
        agent_id: str,
    ):
        self._message = message
        self._credential = credential
        self._agent_id = agent_id

    async def get_message_info(self) -> Dict[str, Any]:
        chat_id = self._message.chat_id
        thread_id = self._message.thread_id or ""
        # Build the reply_instruction for the agent. Telegram's tg_cli
        # contract is `{method, args}`; for replies the canonical method
        # is sendMessage with chat_id + text (and optional thread).
        thread_arg = (
            f', "message_thread_id": "{thread_id}"' if thread_id else ""
        )
        reply_instruction = (
            f'call `tg_cli(method="sendMessage", '
            f'args={{"chat_id": "{chat_id}", "text": "YOUR_REPLY"'
            f"{thread_arg}}})`. Send exactly ONE message. Use plain text "
            f"(parse_mode is not set — Telegram's MarkdownV2 escape rules "
            f"are aggressive; sending raw mrkdwn often produces 400 errors)."
        )

        return {
            "agent_id": self._agent_id,
            "channel_display_name": "Telegram",
            "channel_key": "telegram",
            "room_name": "",  # Telegram chat title is in `chat.title`; deferred
            "room_id": chat_id,
            "room_type": "Group Room" if chat_id.startswith("-") else "Direct Message",
            "sender_display_name": self._message.sender_name or self._message.sender_id,
            "sender_id": self._message.sender_id,
            "timestamp": str(self._message.timestamp_ms),
            "my_channel_id": self._credential.bot_user_id,
            "message_body": self._message.content,
            "send_tool_name": "tg_cli",
            "reply_instruction": reply_instruction,
        }

    async def get_conversation_history(self, limit: int) -> List[Dict[str, Any]]:
        # Telegram Bot API has no equivalent of conversations.history /
        # lark-cli +messages-list. Bots can't read prior messages they
        # didn't already receive. Agent's ChatModule memory carries the
        # only multi-turn context.
        return []

    async def get_room_members(self) -> List[Dict[str, Any]]:
        # Bots can call getChatAdministrators / getChatMemberCount but
        # NOT enumerate non-admin members. Phase 4 returns empty —
        # context builder uses this only for "is group?" rendering, and
        # we already infer that from chat_id sign.
        return []
