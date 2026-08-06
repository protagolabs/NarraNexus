"""
@file_name: test_voice_stream_wiring.py
@date: 2026-08-06
@description: _handle_stream_event — voice bridge wiring guards.

Locks:
- speak AGENT_REPLY_DELTA events feed the bridge; non-speak deltas do not.
- A completed speak PROGRESS carries authoritative text to the bridge and
  never lands in narra_reply_text.
- narra_reply PROGRESS on a voice turn keeps its legacy capture.
- With no bridge (text turn), delta events are ignored exactly as before.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from xyz_agent_context.module.narramessenger_module.matrix_trigger import (
    MatrixTrigger,
    _StreamReplyState,
)
from xyz_agent_context.schema.runtime_message import MessageType


def _cred():
    return SimpleNamespace(agent_id="agent_v")


def _delta(tool="mcp__narramessenger_module__speak", text="Sunny."):
    return SimpleNamespace(
        message_type=MessageType.AGENT_REPLY_DELTA,
        tool_name=tool,
        call_id="c1",
        delta=text,
    )


def _progress(tool, text):
    return SimpleNamespace(
        message_type=MessageType.PROGRESS,
        details={"tool_name": tool, "arguments": {"text": text}, "call_id": "c1"},
    )


@pytest.mark.asyncio
async def test_speak_delta_feeds_bridge_and_nonspeak_does_not():
    state = _StreamReplyState()
    state.voice_bridge = MagicMock()
    state.voice_bridge.on_reply_delta = AsyncMock()
    trigger = MatrixTrigger()

    await trigger._handle_stream_event(_delta(), state, _cred(), "!r")
    state.voice_bridge.on_reply_delta.assert_awaited_once_with(
        call_id="c1", delta="Sunny."
    )

    await trigger._handle_stream_event(
        _delta(tool="mcp__chat_module__send_message_to_user_directly"),
        state,
        _cred(),
        "!r",
    )
    assert state.voice_bridge.on_reply_delta.await_count == 1


@pytest.mark.asyncio
async def test_speak_progress_is_authoritative_and_not_narra_reply():
    state = _StreamReplyState()
    state.voice_bridge = MagicMock()
    trigger = MatrixTrigger()

    await trigger._handle_stream_event(
        _progress("mcp__narramessenger_module__speak", "Sunny today."),
        state,
        _cred(),
        "!r",
    )
    state.voice_bridge.on_segment_text.assert_called_once_with(
        call_id="c1", text="Sunny today."
    )
    assert state.narra_reply_text == ""


@pytest.mark.asyncio
async def test_narra_reply_capture_survives_on_voice_turns():
    state = _StreamReplyState()
    state.voice_bridge = MagicMock()
    trigger = MatrixTrigger()
    await trigger._handle_stream_event(
        _progress("mcp__narramessenger_module__narra_reply", "text version"),
        state,
        _cred(),
        "!r",
    )
    assert state.narra_reply_text == "text version"


@pytest.mark.asyncio
async def test_text_turn_ignores_deltas_as_before():
    state = _StreamReplyState()
    trigger = MatrixTrigger()
    await trigger._handle_stream_event(_delta(), state, _cred(), "!r")
    assert state.narra_reply_text == ""
