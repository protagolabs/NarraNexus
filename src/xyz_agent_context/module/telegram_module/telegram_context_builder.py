"""
@file_name: telegram_context_builder.py
@author: NarraNexus
@date: 2026-03-29
@description: TelegramContextBuilder — Telegram channel prompt constructor

Inherits from ChannelContextBuilderBase and implements Telegram-specific data
fetching. Uses TelegramBotClient for sending actions and TelegramCredential
for bot identity information.

The prompt follows the standard sectioned assembly from the base class:
message info -> sender profile -> conversation history -> message body -> members -> instructions

Key difference from MatrixContextBuilder:
- Conversation history returns empty (Telegram Bot API has no "get chat history" endpoint).
  History is handled upstream by EventMemoryModule via ChatModule's hook pipeline.
- Room members are inferred from the message itself (bot + sender) for group chats.
- No sender extra profile (no Registry equivalent for Telegram).
"""

from __future__ import annotations

from typing import Any, Dict, List

from loguru import logger

from xyz_agent_context.channel.channel_context_builder_base import (
    ChannelContextBuilderBase,
)
from ._telegram_client import TelegramBotClient
from ._telegram_credential_manager import TelegramCredential


class TelegramContextBuilder(ChannelContextBuilderBase):
    """
    Telegram channel prompt constructor.

    Inherits the base class's standard sectioned flow and implements
    Telegram-specific data fetching using the Telegram message dict
    and bot credential information.

    Args:
        message: Raw Telegram Message object dict (from Update.message)
        credential: Agent's TelegramCredential
        client: TelegramBotClient instance
        agent_id: The Agent's ID in our system
    """

    def __init__(
        self,
        message: dict,
        credential: TelegramCredential,
        client: TelegramBotClient,
        agent_id: str,
    ):
        self.message = message
        self.credential = credential
        self.client = client
        self.agent_id = agent_id

    async def get_message_info(self) -> Dict[str, Any]:
        """
        Return message metadata for template rendering.

        Extracts chat and sender information from the Telegram message dict.
        Maps Telegram's chat types to the standard channel format.
        """
        chat = self.message.get("chat", {})
        from_user = self.message.get("from", {})

        # Build sender display name from first_name + last_name
        sender_display_name = (
            (from_user.get("first_name", "") + " " + from_user.get("last_name", "")).strip()
        )

        # Room name: use chat title for groups, or sender name for DMs
        room_name = chat.get("title", "") or sender_display_name

        # Map Telegram chat type to standard room type
        room_type = "Direct Message" if chat.get("type") == "private" else "Group Chat"

        return {
            "agent_id": self.agent_id,
            "channel_display_name": "Telegram",
            "channel_key": "telegram",
            "room_name": room_name,
            "room_id": str(chat.get("id", "")),
            "room_type": room_type,
            "sender_display_name": sender_display_name,
            "sender_id": str(from_user.get("id", "")),
            "timestamp": str(self.message.get("date", "")),
            "my_channel_id": f"@{self.credential.bot_username}",
            "message_body": self.message.get("text", ""),
            "send_tool_name": "telegram_send_message",
        }

    async def get_conversation_history(self, limit: int) -> List[Dict[str, Any]]:
        """
        Return recent conversation history for this chat.

        Returns empty list. The Telegram Bot API does not provide a
        "get chat history" endpoint for bots. Conversation history is
        handled upstream by EventMemoryModule via ChatModule's hook pipeline,
        which injects relevant past interactions into the agent's context.

        Args:
            limit: Max number of messages (unused)

        Returns:
            Empty list
        """
        return []

    async def get_room_members(self) -> List[Dict[str, Any]]:
        """
        Return chat member list.

        For group chats, returns basic info about the bot and the message sender.
        For DMs (private chats), returns empty list since the base class already
        handles 1:1 conversations without needing an explicit member list.

        Returns:
            List of members for group chats, empty list for DMs
        """
        chat_type = self.message.get("chat", {}).get("type", "private")
        if chat_type == "private":
            return []

        from_user = self.message.get("from", {})
        sender_display_name = (
            (from_user.get("first_name", "") + " " + from_user.get("last_name", "")).strip()
        )

        return [
            {
                "display_name": f"@{self.credential.bot_username}",
                "user_id": str(self.credential.bot_id),
                "role": "bot",
            },
            {
                "display_name": sender_display_name,
                "user_id": str(from_user.get("id", "")),
                "role": "member",
            },
        ]

    async def get_sender_extra_profile(self) -> str:
        """
        Return channel-specific extra sender profile info.

        Returns empty string — Telegram has no equivalent to Matrix's
        Registry for capability lookups.

        Returns:
            Empty string
        """
        return ""
