"""
@file_name: test_telegram_context_builder.py
@date: 2026-05-09
@description: Tests for TelegramContextBuilder — message_info shape,
empty-history guarantee, and chat-id-sign room_type derivation.

Why this file exists:
    Telegram has no Bot-API equivalent of ``conversations.history``,
    so ``get_conversation_history`` MUST return ``[]`` unconditionally.
    The reply_instruction also needs ``thread_id`` baked in for forum
    topics; otherwise the agent's reply lands in the wrong place.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.module.telegram_module._telegram_credential_manager import (
    TelegramCredential,
)
from xyz_agent_context.module.telegram_module.telegram_context_builder import (
    TelegramContextBuilder,
)
from xyz_agent_context.schema.parsed_message import (
    ChatType,
    MessageContentType,
    ParsedMessage,
)


def _cred() -> TelegramCredential:
    return TelegramCredential(
        agent_id="agent_a",
        bot_token="1234:tok",
        bot_user_id="1001",
        bot_username="acme_bot",
    )


def _msg(
    chat_id: str = "99",
    thread_id: str | None = None,
    chat_type: ChatType = ChatType.PRIVATE,
) -> ParsedMessage:
    return ParsedMessage(
        message_id="7",
        chat_id=chat_id,
        sender_id="42",
        sender_name="Ada Lovelace",
        content="hello there",
        content_type=MessageContentType.TEXT,
        chat_type=chat_type,
        timestamp_ms=1700000000000,
        thread_id=thread_id,
    )


@pytest.mark.asyncio
async def test_get_message_info_shape_for_dm():
    builder = TelegramContextBuilder(
        message=_msg(), credential=_cred(), agent_id="agent_a"
    )

    info = await builder.get_message_info()

    assert info["agent_id"] == "agent_a"
    assert info["channel_key"] == "telegram"
    assert info["channel_display_name"] == "Telegram"
    assert info["room_id"] == "99"
    assert info["sender_id"] == "42"
    assert info["sender_display_name"] == "Ada Lovelace"
    assert info["my_channel_id"] == "1001"
    assert info["message_body"] == "hello there"
    assert info["send_tool_name"] == "tg_cli"
    assert info["room_type"] == "Direct Message"
    # Reply instruction baked-in chat_id + sendMessage shape
    assert "sendMessage" in info["reply_instruction"]
    assert "99" in info["reply_instruction"]


@pytest.mark.asyncio
async def test_get_message_info_room_type_for_group():
    """Negative chat_id → group/supergroup/channel."""
    builder = TelegramContextBuilder(
        message=_msg(chat_id="-1001234567890", chat_type=ChatType.GROUP),
        credential=_cred(),
        agent_id="agent_a",
    )

    info = await builder.get_message_info()
    assert info["room_type"] == "Group Room"


@pytest.mark.asyncio
async def test_reply_instruction_includes_thread_id_when_set():
    builder = TelegramContextBuilder(
        message=_msg(thread_id="555"),
        credential=_cred(),
        agent_id="agent_a",
    )

    info = await builder.get_message_info()
    assert "message_thread_id" in info["reply_instruction"]
    assert "555" in info["reply_instruction"]


@pytest.mark.asyncio
async def test_reply_instruction_omits_thread_id_when_unset():
    builder = TelegramContextBuilder(
        message=_msg(thread_id=None),
        credential=_cred(),
        agent_id="agent_a",
    )

    info = await builder.get_message_info()
    assert "message_thread_id" not in info["reply_instruction"]


@pytest.mark.asyncio
async def test_get_conversation_history_always_empty():
    """Telegram bots cannot read messages they didn't already receive."""
    builder = TelegramContextBuilder(
        message=_msg(), credential=_cred(), agent_id="agent_a"
    )
    assert await builder.get_conversation_history(limit=50) == []


@pytest.mark.asyncio
async def test_get_room_members_returns_empty():
    builder = TelegramContextBuilder(
        message=_msg(), credential=_cred(), agent_id="agent_a"
    )
    assert await builder.get_room_members() == []
