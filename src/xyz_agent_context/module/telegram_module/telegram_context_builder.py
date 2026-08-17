"""
@file_name: telegram_context_builder.py
@date: 2026-05-09
@description: Build execution context for Telegram-triggered messages.

Inherits ChannelContextBuilderBase. Unlike Slack/Lark, Telegram's Bot
API does NOT expose a server-side history endpoint (bots can only see
events they themselves received). To avoid agents replying with zero
context — observed 2026-05-13 where the agent treated "再试一下" as
"retry the Telegram channel test" instead of "retry the weather query"
because no recent turns were in the prompt — we fall back to the local
local inbox record, which ``InboxRecorder`` populates for both inbound user
messages and outbound bot replies under
``thread_id = im_thread_id("telegram", chat_id)``.

Note this is one of the two places the inbox record is also OPERATIONAL: for a
channel with no history API it IS the agent's conversation memory, not just
something a person reads. The 2026-08-17 decoupling moved this reader with the
writer — leaving it on ``bus_messages`` would have made Telegram forget every
conversation.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from xyz_agent_context.channel.channel_prompts import (
    ROOM_TYPE_DIRECT,
    ROOM_TYPE_GROUP,
)
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
        db_client: Any = None,
    ):
        self._message = message
        self._credential = credential
        self._agent_id = agent_id
        # Optional — when present, ``get_conversation_history`` reads the
        # inbox record. None falls back to empty history (tests, or
        # process startup before the trigger has set _db).
        self._db = db_client

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
            "room_type": (
                ROOM_TYPE_GROUP if chat_id.startswith("-") else ROOM_TYPE_DIRECT
            ),
            "sender_display_name": self._message.sender_name or self._message.sender_id,
            "sender_id": self._message.sender_id,
            "timestamp": str(self._message.timestamp_ms),
            "my_channel_id": self._credential.bot_user_id,
            "message_body": self._message.content,
            "send_tool_name": "tg_cli",
            "reply_instruction": reply_instruction,
        }

    async def get_conversation_history(self, limit: int) -> List[Dict[str, Any]]:
        """Read recent turns from the local inbox record.

        Telegram Bot API has no equivalent of conversations.history, so we keep
        our own log: ``InboxRecorder`` writes every inbound user message and
        every outbound bot reply under ``im_thread_id("telegram", chat_id)``,
        each row carrying a ``direction`` of "in" or "out". Query the most
        recent ``limit + 1`` rows and drop the current trigger message itself.
        """
        if not self._db or not self._message.chat_id:
            return []

        from xyz_agent_context.channel.inbox_recorder import (
            OUTBOUND,
            im_thread_id,
        )

        thread_id = im_thread_id("telegram", self._message.chat_id)
        # Pull a bit more than `limit` so we can drop the current message
        # without ending up short.
        fetch_n = max(limit + 5, 10)
        try:
            rows = await self._db.get(
                "inbox_thread_messages",
                {"thread_id": thread_id},
                limit=fetch_n,
                order_by="created_at DESC",
            )
        except Exception as e:  # noqa: BLE001 — history is best-effort
            logger.warning(
                f"[telegram:{self._agent_id}] history fetch failed "
                f"(thread={thread_id}): {type(e).__name__}: {e}"
            )
            return []

        # ``rows`` is newest-first; we want chronological order in the
        # prompt, matching Slack/Lark renderers.
        current_id = self._message.message_id
        normalized: List[Dict[str, Any]] = []
        for row in reversed(rows):
            content = row.get("content", "") or ""
            row_msg_id = row.get("message_id", "") or ""
            # Skip the current trigger message itself — already rendered
            # as the "Current Message" section in the prompt template.
            if current_id and row_msg_id == current_id:
                continue
            # `direction` states whose line this is. The old form compared
            # `from_agent` against the agent id, which the record layer no
            # longer stores as a bus sender.
            is_bot = row.get("direction") == OUTBOUND
            sender = "Me (bot)" if is_bot else (
                self._message.sender_name or row.get("sender_name") or ""
            )
            normalized.append({
                "timestamp": str(row.get("created_at", "")),
                "sender": sender,
                "body": content,
            })

        # If we pulled more than limit (because we over-fetched), keep
        # the newest ``limit`` entries.
        if len(normalized) > limit:
            normalized = normalized[-limit:]
        return normalized

    async def get_room_members(self) -> List[Dict[str, Any]]:
        # Bots can call getChatAdministrators / getChatMemberCount but
        # NOT enumerate non-admin members. Phase 4 returns empty —
        # context builder uses this only for "is group?" rendering, and
        # we already infer that from chat_id sign.
        return []
