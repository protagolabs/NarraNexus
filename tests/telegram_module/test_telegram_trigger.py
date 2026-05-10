"""
@file_name: test_telegram_trigger.py
@date: 2026-05-09
@description: Tests for TelegramTrigger event parsing + extract_output.

Why this file exists:
    The trigger sits between Telegram's long-poll and the channel
    pipeline. Hot decisions — "is this an event we care about?", "is
    it our own bot's echo?", "what did the agent actually send?" —
    live in narrow methods that are easy to cover without standing
    up a real long-poll loop.

    The ``extract_output`` regression test mirrors the Phase 3 Slack
    bug: the inbox MUST show the bot's reply text (scraped from
    ``tg_cli`` sendMessage args), NOT ``result.output_text`` which
    leaks the agent's reasoning.
"""
from __future__ import annotations

import json

import pytest

from xyz_agent_context.module.telegram_module._telegram_credential_manager import (
    TelegramCredential,
)
from xyz_agent_context.module.telegram_module.telegram_trigger import (
    TelegramTrigger,
)
from xyz_agent_context.schema.parsed_message import ChatType


def _cred(bot_user_id: str = "1001", bot_username: str = "acme_bot") -> TelegramCredential:
    return TelegramCredential(
        agent_id="agent_a",
        bot_token="1234:tok",
        bot_user_id=bot_user_id,
        bot_username=bot_username,
    )


def _msg(**overrides) -> dict:
    """Minimal Telegram update payload."""
    base = {
        "update_id": 100,
        "message": {
            "message_id": 7,
            "date": 1700000000,
            "from": {"id": 42, "first_name": "Ada", "last_name": "Lovelace"},
            "chat": {"id": 99, "type": "private"},
            "text": "hello",
        },
    }
    base["message"].update(overrides)
    return base


# ── parse_event ─────────────────────────────────────────────────────────


def test_parse_event_normal_text_message():
    trigger = TelegramTrigger()
    parsed = trigger.parse_event(_msg())

    assert parsed is not None
    assert parsed.message_id == "7"
    assert parsed.chat_id == "99"
    assert parsed.sender_id == "42"
    assert parsed.sender_name == "Ada Lovelace"
    assert parsed.content == "hello"
    assert parsed.chat_type == ChatType.PRIVATE
    assert parsed.timestamp_ms == 1700000000 * 1000


def test_parse_event_no_text_returns_none():
    """Sticker / media / voice — Phase 4 is text-only."""
    trigger = TelegramTrigger()
    parsed = trigger.parse_event(
        _msg(text="", sticker={"emoji": ":-)"})
    )
    assert parsed is None


def test_parse_event_with_message_thread_id():
    trigger = TelegramTrigger()
    parsed = trigger.parse_event(_msg(message_thread_id=12345))

    assert parsed is not None
    assert parsed.thread_id == "12345"


def test_parse_event_with_reply_to_message():
    trigger = TelegramTrigger()
    parsed = trigger.parse_event(
        _msg(reply_to_message={"message_id": 6})
    )

    assert parsed is not None
    assert parsed.reply_to_message_id == "6"


def test_parse_event_with_mention_entity():
    trigger = TelegramTrigger()
    text = "hi @acme_bot please"
    parsed = trigger.parse_event(
        _msg(
            text=text,
            entities=[{"type": "mention", "offset": 3, "length": 9}],
        )
    )

    assert parsed is not None
    assert "acme_bot" in parsed.mentions


def test_parse_event_text_mention_uses_user_id():
    """Inline mention without @username — use the embedded user.id."""
    trigger = TelegramTrigger()
    parsed = trigger.parse_event(
        _msg(
            text="poke",
            entities=[
                {
                    "type": "text_mention",
                    "offset": 0,
                    "length": 4,
                    "user": {"id": 555},
                }
            ],
        )
    )

    assert parsed is not None
    assert "555" in parsed.mentions


def test_parse_event_supergroup_chat_classified_group():
    trigger = TelegramTrigger()
    parsed = trigger.parse_event(
        _msg(chat={"id": -1001234567890, "type": "supergroup", "title": "Forum"})
    )

    assert parsed is not None
    assert parsed.chat_type == ChatType.GROUP
    assert parsed.chat_id == "-1001234567890"


def test_parse_event_no_message_returns_none():
    trigger = TelegramTrigger()
    assert trigger.parse_event({"update_id": 1}) is None


# ── is_echo ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_is_echo_detects_bot_user_id():
    trigger = TelegramTrigger()
    parsed = trigger.parse_event(
        _msg(**{"from": {"id": 1001, "first_name": "Bot"}})
    )
    assert parsed is not None
    assert await trigger.is_echo(parsed, _cred(bot_user_id="1001")) is True


@pytest.mark.asyncio
async def test_is_echo_false_for_human_sender():
    trigger = TelegramTrigger()
    parsed = trigger.parse_event(_msg())
    assert parsed is not None
    assert await trigger.is_echo(parsed, _cred(bot_user_id="1001")) is False


@pytest.mark.asyncio
async def test_is_echo_false_when_credential_has_no_bot_user_id():
    trigger = TelegramTrigger()
    parsed = trigger.parse_event(
        _msg(**{"from": {"id": 1001, "first_name": "Bot"}})
    )
    assert parsed is not None
    assert await trigger.is_echo(parsed, _cred(bot_user_id="")) is False


# ── extract_output: PHASE-3 REGRESSION ────────────────────────────────


def test_extract_output_reads_tg_cli_args_not_output_text():
    """REGRESSION: inbox shows what was sent via tg_cli sendMessage,
    NOT ``result.output_text`` which leaks the agent's chain-of-thought
    (the Phase 3 Slack bug — must not recur here)."""
    trigger = TelegramTrigger()

    class _R:
        output_text = "My thought process: User asked X, I should reply Y"
        raw_items = [
            {
                "item": {
                    "type": "tool_call_item",
                    "tool_name": "mcp__telegram_module__tg_cli",
                    "arguments": {
                        "agent_id": "agent_a",
                        "method": "sendMessage",
                        "args": {"chat_id": "99", "text": "Hello, I'm here!"},
                    },
                }
            }
        ]

    out = trigger.extract_output(_R(), None, _cred())
    assert out == "Hello, I'm here!"
    assert "thought process" not in out


def test_extract_output_returns_silent_sentinel_when_no_send_message():
    trigger = TelegramTrigger()

    class _R:
        output_text = ""
        raw_items: list = []

    out = trigger.extract_output(_R(), None, _cred())
    assert out == "(stayed silent)"


def test_extract_output_skips_non_send_message_calls():
    """setMessageReaction / deleteMessage / etc. are NOT user-visible reply text."""
    trigger = TelegramTrigger()

    class _R:
        output_text = ""
        raw_items = [
            {
                "item": {
                    "type": "tool_call_item",
                    "tool_name": "mcp__telegram_module__tg_cli",
                    "arguments": {
                        "method": "setMessageReaction",
                        "args": {
                            "chat_id": "99",
                            "message_id": 7,
                            "reaction": [{"type": "emoji", "emoji": "👍"}],
                        },
                    },
                }
            }
        ]

    out = trigger.extract_output(_R(), None, _cred())
    assert out == "(stayed silent)"


def test_extract_output_handles_arguments_as_json_string():
    """When ``arguments`` arrives serialized, parse it before scraping."""
    trigger = TelegramTrigger()

    class _R:
        output_text = ""
        raw_items = [
            {
                "item": {
                    "type": "tool_call_item",
                    "tool_name": "mcp__telegram_module__tg_cli",
                    "arguments": json.dumps(
                        {
                            "method": "sendMessage",
                            "args": {
                                "chat_id": "99",
                                "text": "string-encoded reply",
                            },
                        }
                    ),
                }
            }
        ]

    out = trigger.extract_output(_R(), None, _cred())
    assert out == "string-encoded reply"


# ── resolve_sender_name fallback ───────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_sender_name_returns_sender_id_fallback():
    """Telegram has no user-info-by-id endpoint without chat context —
    the fallback path returns the sender_id as a stable identifier."""
    trigger = TelegramTrigger()
    name = await trigger.resolve_sender_name("42", _cred())
    assert name == "42"
