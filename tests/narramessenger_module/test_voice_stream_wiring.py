"""
@file_name: test_voice_stream_wiring.py
@date: 2026-08-06
@description: _handle_stream_event — voice bridge wiring guards.

Locks:
- The live voice stream is CLAIMED by the first trigger-owned reply tool
  (speak or narra_reply) that produces a delta or a completed text; only
  the claimant feeds the bridge.
- Claimant deltas (AGENT_REPLY_DELTA) feed the bridge; deltas from
  non-reply tools never do.
- A completed claimant PROGRESS carries authoritative text to the bridge
  and never lands in narra_reply_text.
- Reply-tool events from a NON-claimant fall back to the legacy capture
  (narra_reply_text) so finalize keeps its existing precedence chain.
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
async def test_narra_reply_claims_bridge_when_unclaimed():
    """A narra_reply on a voice turn with no prior speak activity claims
    the live stream: its text goes to the bridge, NOT to the legacy
    fresh-send capture (which would deliver a duplicate at finalize)."""
    state = _StreamReplyState()
    state.voice_bridge = MagicMock()
    trigger = MatrixTrigger()
    await trigger._handle_stream_event(
        _progress("mcp__narramessenger_module__narra_reply", "text version"),
        state,
        _cred(),
        "!r",
    )
    state.voice_bridge.on_segment_text.assert_called_once_with(
        call_id="c1", text="text version"
    )
    assert state.narra_reply_text == ""


@pytest.mark.asyncio
async def test_narra_reply_delta_feeds_bridge():
    """narra_reply arg deltas stream into the bridge exactly like speak
    deltas — the model's reply-tool choice must not decide whether the
    caller hears the first word live."""
    state = _StreamReplyState()
    state.voice_bridge = MagicMock()
    state.voice_bridge.on_reply_delta = AsyncMock()
    trigger = MatrixTrigger()

    await trigger._handle_stream_event(
        _delta(tool="mcp__narramessenger_module__narra_reply", text="Guang"),
        state,
        _cred(),
        "!r",
    )
    state.voice_bridge.on_reply_delta.assert_awaited_once_with(
        call_id="c1", delta="Guang"
    )


@pytest.mark.asyncio
async def test_speak_claim_sends_later_narra_reply_to_legacy_capture():
    """Once speak has claimed the stream, a trailing narra_reply keeps the
    legacy capture so finalize's precedence (spoken wins; narra_reply is
    the fallback when nothing was spoken) is unchanged."""
    state = _StreamReplyState()
    state.voice_bridge = MagicMock()
    state.voice_bridge.on_reply_delta = AsyncMock()
    trigger = MatrixTrigger()

    await trigger._handle_stream_event(_delta(), state, _cred(), "!r")
    await trigger._handle_stream_event(
        _progress("mcp__narramessenger_module__narra_reply", "also noted"),
        state,
        _cred(),
        "!r",
    )
    state.voice_bridge.on_segment_text.assert_not_called()
    assert state.narra_reply_text == "also noted"


@pytest.mark.asyncio
async def test_narra_reply_claim_sends_later_speak_to_legacy_capture():
    """Symmetric guard: once narra_reply claimed the stream, a trailing
    speak call must not open a second bridge segment (the 2026-08-07
    duplicate-playback shape) — it falls back to the text-turn capture."""
    state = _StreamReplyState()
    state.voice_bridge = MagicMock()
    state.voice_bridge.on_reply_delta = AsyncMock()
    trigger = MatrixTrigger()

    await trigger._handle_stream_event(
        _delta(tool="mcp__narranexus_module__x", text="ignored"), state, _cred(), "!r"
    )
    await trigger._handle_stream_event(
        _delta(tool="mcp__narramessenger_module__narra_reply", text="the answer"),
        state,
        _cred(),
        "!r",
    )
    await trigger._handle_stream_event(
        _progress("mcp__narramessenger_module__speak", "spoken tail"),
        state,
        _cred(),
        "!r",
    )
    state.voice_bridge.on_segment_text.assert_not_called()
    assert state.narra_reply_text == "spoken tail"


@pytest.mark.asyncio
async def test_narra_reply_stream_end_to_end_no_duplicate_delivery():
    """Real-bridge scenario: narra_reply deltas + authoritative completion
    produce ONE live lifecycle (base + final edit) with the final text —
    never a second plain send of the same content."""
    from xyz_agent_context.module.narramessenger_module._voice_delivery import (
        VoiceDeliveryBridge,
    )

    sent: list[dict] = []

    async def _send(content: dict) -> str:
        sent.append(content)
        return f"$e{len(sent)}"

    clock_now = [0.0]
    bridge = VoiceDeliveryBridge(send=_send, clock=lambda: clock_now[0])
    state = _StreamReplyState()
    state.voice_bridge = bridge
    trigger = MatrixTrigger()

    for i, piece in enumerate(["广州是", "华南的中心城市。"]):
        clock_now[0] += 1.0
        await trigger._handle_stream_event(
            SimpleNamespace(
                message_type=MessageType.AGENT_REPLY_DELTA,
                tool_name="mcp__narramessenger_module__narra_reply",
                call_id="prov1",
                delta=piece,
            ),
            state,
            _cred(),
            "!r",
        )
    # Real streams carry an EMPTY call_id on PROGRESS (run_collector does
    # not propagate tool_call_id — dev probe 2026-08-07), so the bridge's
    # raw-prefix judge is what keeps this the SAME segment.
    await trigger._handle_stream_event(
        SimpleNamespace(
            message_type=MessageType.PROGRESS,
            details={
                "tool_name": "mcp__narramessenger_module__narra_reply",
                "arguments": {"text": "广州是华南的中心城市。"},
                "call_id": "",
            },
        ),
        state,
        _cred(),
        "!r",
    )
    spoken, ok = await bridge.close()
    assert ok is True
    assert spoken == "广州是华南的中心城市。"
    assert state.narra_reply_text == ""  # nothing left for a duplicate send
    final = sent[-1]
    final_body = (
        final["m.new_content"]["body"] if "m.new_content" in final else final["body"]
    )
    assert final_body == "广州是华南的中心城市。"


@pytest.mark.asyncio
async def test_narra_reply_progress_only_still_delivers_via_bridge():
    """Provider without arg-delta streaming: the completed narra_reply
    text alone must still ride the bridge (one final send), keeping the
    no-delta path alive."""
    from xyz_agent_context.module.narramessenger_module._voice_delivery import (
        VoiceDeliveryBridge,
    )

    sent: list[dict] = []

    async def _send(content: dict) -> str:
        sent.append(content)
        return f"$e{len(sent)}"

    bridge = VoiceDeliveryBridge(send=_send)
    state = _StreamReplyState()
    state.voice_bridge = bridge
    trigger = MatrixTrigger()

    await trigger._handle_stream_event(
        _progress("mcp__narramessenger_module__narra_reply", "只有整段文本。"),
        state,
        _cred(),
        "!r",
    )
    spoken, ok = await bridge.close()
    assert (spoken, ok) == ("只有整段文本。", True)
    assert state.narra_reply_text == ""
    assert len(sent) == 1 and sent[0]["body"] == "只有整段文本。"


@pytest.mark.asyncio
async def test_text_turn_ignores_deltas_as_before():
    state = _StreamReplyState()
    trigger = MatrixTrigger()
    await trigger._handle_stream_event(_delta(), state, _cred(), "!r")
    assert state.narra_reply_text == ""


def test_voice_timing_line_shape():
    from xyz_agent_context.module.narramessenger_module.matrix_trigger import (
        _format_voice_timing,
    )

    line = _format_voice_timing(
        agent_id="agent_v",
        room_id="!call:h",
        rtc_session_id="rtc-s1",
        received_at=10.0,
        applied_at=10.001,
        request_started_at=10.05,
        first_delta_at=11.2,
        first_sent_at=11.3,
        finalized_at=14.0,
    )
    assert line == (
        "[voice-timing] agent=agent_v room=!call:h rtc_session=rtc-s1 "
        "applied_s=0.00 request_s=0.05 first_token_s=1.20 "
        "first_live_s=1.30 finalized_s=4.00"
    )


def test_voice_timing_line_tolerates_missing_stamps():
    from xyz_agent_context.module.narramessenger_module.matrix_trigger import (
        _format_voice_timing,
    )

    line = _format_voice_timing(
        agent_id="a",
        room_id="!r",
        rtc_session_id="s",
        received_at=10.0,
        applied_at=10.0,
        request_started_at=10.0,
        first_delta_at=None,
        first_sent_at=None,
        finalized_at=None,
    )
    assert "first_token_s=-1.00" in line
    assert "first_live_s=-1.00" in line
    assert "finalized_s=-1.00" in line


@pytest.mark.asyncio
async def test_speak_on_text_turn_is_delivered_not_dead():
    """Review finding #2: without a bridge (text turn), a speak call must
    still reach the room via the legacy capture — never a silent ok:true."""
    state = _StreamReplyState()
    trigger = MatrixTrigger()
    await trigger._handle_stream_event(
        _progress("mcp__narramessenger_module__speak", "spoken by mistake"),
        state,
        _cred(),
        "!r",
    )
    assert state.narra_reply_text == "spoken by mistake"

    # Segments concatenate (speak is a multi-call tool).
    await trigger._handle_stream_event(
        _progress("mcp__narramessenger_module__speak", "second part"),
        state,
        _cred(),
        "!r",
    )
    assert state.narra_reply_text == "spoken by mistake second part"


def test_atomic_extract_output_captures_speak_segments():
    """Review finding #16: the atomic path (STREAMING_ENABLED=False) must
    not leave speak as a dead tool either — extract_output joins speak
    segments when no narra_reply was called."""
    trigger = MatrixTrigger()

    def _tool(name, text):
        return {"item": {"type": "tool_call_item", "tool_name": name,
                         "arguments": {"text": text}}}

    result = SimpleNamespace(raw_items=[
        _tool("mcp__narramessenger_module__speak", "first part."),
        _tool("mcp__narramessenger_module__speak", "second part."),
    ])
    out = trigger.extract_output(result, None, None)
    assert out == "first part. second part."

    # narra_reply stays THE reply when both are present.
    result2 = SimpleNamespace(raw_items=[
        _tool("mcp__narramessenger_module__speak", "spoken"),
        _tool("mcp__narramessenger_module__narra_reply", "the real answer"),
    ])
    assert trigger.extract_output(result2, None, None) == "the real answer"


def test_atomic_and_streaming_agree_on_mixed_tool_order():
    """Review finding #22: the two paths must give the same answer for the
    same call sequence — ordered fold with speak appending and narra_reply
    replacing (streaming's actual semantics)."""
    trigger = MatrixTrigger()

    def _tool(name, text):
        return {"item": {"type": "tool_call_item", "tool_name": name,
                         "arguments": {"text": text}}}

    # narra_reply first, speak after -> concatenation (streaming behavior).
    result = SimpleNamespace(raw_items=[
        _tool("mcp__narramessenger_module__narra_reply", "the answer."),
        _tool("mcp__narramessenger_module__speak", "one more thing."),
    ])
    assert trigger.extract_output(result, None, None) == "the answer. one more thing."

    # speak first, narra_reply after -> narra_reply replaces (last writer).
    result2 = SimpleNamespace(raw_items=[
        _tool("mcp__narramessenger_module__speak", "spoken"),
        _tool("mcp__narramessenger_module__narra_reply", "the real answer"),
    ])
    assert trigger.extract_output(result2, None, None) == "the real answer"
