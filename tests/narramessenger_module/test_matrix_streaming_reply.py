"""
@file_name: test_matrix_streaming_reply.py
@date: 2026-07-03
@description: MatrixTrigger streaming reply — "thinking + agent-driven
progress" model (2026-07-03 redesign).

Contract:
- A ``💭 Thinking…`` placeholder is shipped immediately at turn start
  (tested via the sub-methods here; the send itself lives in
  ``_build_and_run_agent_streaming``).
- Raw ``AGENT_RESPONSE`` token deltas and ``AGENT_THINKING`` are IGNORED —
  the room never shows the agent's streamed output/reasoning. (This is the
  whole point of the redesign: no jitter.)
- ``narra_progress(text)`` → ``m.replace``-edit the placeholder to that
  status, rate-limited by ``STREAM_PROGRESS_MIN_INTERVAL_MS``.
- ``narra_reply(text)`` → captured; on finalise it OVERWRITES the
  placeholder (or fresh-sends if the placeholder never shipped).
- Silent (no ``narra_reply``) → redact the placeholder.
- ``STREAMING_ENABLED=False`` bypasses all of this → atomic path.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from xyz_agent_context.module.narramessenger_module._narramessenger_credential_manager import (
    NarramessengerCredential,
)
from xyz_agent_context.module.narramessenger_module import matrix_trigger as mt_mod
from xyz_agent_context.module.narramessenger_module.matrix_trigger import (
    MatrixTrigger,
    _StreamReplyState,
)
from xyz_agent_context.schema.runtime_message import MessageType


ROOM = "!room:matrix.netmind.chat"


def _cred() -> NarramessengerCredential:
    return NarramessengerCredential(
        agent_id="agent_x",
        bearer_token="narra-tok",
        backend_base_url="https://api.netmind.chat",
        matrix_homeserver_url="https://matrix.netmind.chat",
        matrix_user_id="@agent-x:matrix.netmind.chat",
        matrix_access_token="syt_x",
    )


def _delta(text: str):
    return SimpleNamespace(message_type=MessageType.AGENT_RESPONSE, delta=text)


def _thinking(text: str):
    return SimpleNamespace(
        message_type=MessageType.AGENT_THINKING, thinking_content=text
    )


def _tool_call(tool_name: str, arguments):
    """ProgressMessage-shaped event — how the runtime actually emits tool
    calls (message_type=PROGRESS, details.tool_name/arguments). ``arguments``
    may be a dict or a JSON string (both shapes occur in the live SDK)."""
    return SimpleNamespace(
        message_type=MessageType.PROGRESS,
        details={"tool_name": tool_name, "arguments": arguments},
    )


@pytest.fixture
def trigger(monkeypatch):
    t = MatrixTrigger()
    t.STREAM_PROGRESS_MIN_INTERVAL_MS = 0  # don't rate-limit in tests

    calls = {"send": [], "edit": [], "redact": []}

    async def _fake_send(*, homeserver, token, room_id, content, txn_id=None):
        calls["send"].append({"room": room_id, "body": content.get("body", "")})
        return f"$sent{len(calls['send'])}"

    async def _fake_edit(
        *, homeserver, token, room_id, original_event_id, new_body, txn_id=None
    ):
        calls["edit"].append(
            {"room": room_id, "orig": original_event_id, "new_body": new_body}
        )
        return f"$edit{len(calls['edit'])}"

    async def _fake_redact(
        *, homeserver, token, room_id, event_id, reason="", txn_id=None
    ):
        calls["redact"].append(
            {"room": room_id, "event_id": event_id, "reason": reason}
        )
        return f"$redact{len(calls['redact'])}"

    monkeypatch.setattr(mt_mod, "matrix_room_send", _fake_send)
    monkeypatch.setattr(mt_mod, "matrix_room_edit", _fake_edit)
    monkeypatch.setattr(mt_mod, "matrix_room_redact", _fake_redact)
    return t, calls


# ── raw output / thinking are ignored ───────────────────────────────────
@pytest.mark.asyncio
async def test_agent_response_delta_ignored(trigger):
    """Token deltas must NOT touch the room — no send, no edit."""
    t, calls = trigger
    state = _StreamReplyState(placeholder_event_id="$sent1")
    await t._handle_stream_event(_delta("Hello "), state, _cred(), ROOM)
    await t._handle_stream_event(_delta("world"), state, _cred(), ROOM)
    assert calls["send"] == []
    assert calls["edit"] == []


@pytest.mark.asyncio
async def test_agent_thinking_ignored(trigger):
    t, calls = trigger
    state = _StreamReplyState(placeholder_event_id="$sent1")
    await t._handle_stream_event(_thinking("let me think..."), state, _cred(), ROOM)
    assert calls["send"] == []
    assert calls["edit"] == []


# ── narra_progress ──────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_narra_progress_edits_placeholder(trigger):
    t, calls = trigger
    state = _StreamReplyState(placeholder_event_id="$sent1")
    await t._handle_stream_event(
        _tool_call("mcp__narramessenger_module__narra_progress",
                   {"text": "Reading the chart…"}),
        state, _cred(), ROOM,
    )
    assert calls["edit"] == [
        {"room": ROOM, "orig": "$sent1", "new_body": "Reading the chart…"}
    ]


@pytest.mark.asyncio
async def test_narra_progress_noop_without_placeholder(trigger):
    """If the placeholder never shipped, progress edits are skipped (no
    event to edit)."""
    t, calls = trigger
    state = _StreamReplyState()  # no placeholder
    await t._handle_stream_event(
        _tool_call("narra_progress", {"text": "working…"}), state, _cred(), ROOM
    )
    assert calls["edit"] == []


@pytest.mark.asyncio
async def test_narra_progress_rate_limited(trigger):
    """Two progress updates inside the min-interval → only the first edits."""
    t, calls = trigger
    t.STREAM_PROGRESS_MIN_INTERVAL_MS = 10_000_000  # effectively "once"
    state = _StreamReplyState(placeholder_event_id="$sent1")
    await t._handle_stream_event(
        _tool_call("narra_progress", {"text": "step 1"}), state, _cred(), ROOM
    )
    await t._handle_stream_event(
        _tool_call("narra_progress", {"text": "step 2"}), state, _cred(), ROOM
    )
    assert len(calls["edit"]) == 1
    assert calls["edit"][0]["new_body"] == "step 1"


# ── narra_reply capture ─────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_narra_reply_captured(trigger):
    t, _ = trigger
    state = _StreamReplyState(placeholder_event_id="$sent1")
    await t._handle_stream_event(
        _tool_call("web_search", {"query": "irrelevant"}), state, _cred(), ROOM
    )
    assert state.narra_reply_text == ""
    await t._handle_stream_event(
        _tool_call("narra_reply", {"text": "Final answer."}), state, _cred(), ROOM
    )
    assert state.narra_reply_text == "Final answer."


@pytest.mark.asyncio
async def test_narra_reply_last_wins(trigger):
    t, _ = trigger
    state = _StreamReplyState(placeholder_event_id="$sent1")
    await t._handle_stream_event(
        _tool_call("narra_reply", {"text": "first"}), state, _cred(), ROOM
    )
    await t._handle_stream_event(
        _tool_call("narra_reply", {"text": "revised final"}), state, _cred(), ROOM
    )
    assert state.narra_reply_text == "revised final"


@pytest.mark.asyncio
async def test_narra_reply_mcp_prefixed_and_json_args(trigger):
    """MCP-prefixed tool name (substring match) + arguments as a JSON
    string — both must work; this exact combo was the first-live-turn bug."""
    t, _ = trigger
    state = _StreamReplyState(placeholder_event_id="$sent1")
    await t._handle_stream_event(
        _tool_call("mcp__narramessenger_module__narra_reply",
                   '{"text": "from a json blob"}'),
        state, _cred(), ROOM,
    )
    assert state.narra_reply_text == "from a json blob"


# ── finalise: with reply ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_finalise_with_reply_edits_placeholder(trigger):
    t, calls = trigger
    state = _StreamReplyState(placeholder_event_id="$sent1")
    await t._finalize_stream_with_reply(_cred(), ROOM, state, "Clean final answer.")
    assert calls["edit"] == [
        {"room": ROOM, "orig": "$sent1", "new_body": "Clean final answer."}
    ]


@pytest.mark.asyncio
async def test_finalise_with_reply_no_placeholder_sends_fresh(trigger, monkeypatch):
    """Placeholder send failed (send_failure / no id) → fall back to the
    retry-aware _send_matrix_reply."""
    t, _ = trigger
    state = _StreamReplyState()  # no placeholder
    fake_send = AsyncMock(return_value=True)
    monkeypatch.setattr(t, "_send_matrix_reply", fake_send)
    await t._finalize_stream_with_reply(_cred(), ROOM, state, "Instant answer.")
    assert fake_send.await_count == 1
    args = fake_send.await_args
    assert args.args[1] == ROOM
    assert args.args[2] == "Instant answer."


# ── finalise: silent ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_finalise_silent_edits_placeholder_to_silent_marker(trigger):
    """Silent-not-reply (no error) → edit to STREAM_SILENT_MARKER, NOT
    redact. Redaction rendered as 'message deleted' in every Matrix
    client — misleading when the silent path was intentional."""
    t, calls = trigger
    state = _StreamReplyState(placeholder_event_id="$sent1")
    await t._finalize_stream_silent(_cred(), ROOM, state)
    assert calls["redact"] == []
    assert calls["edit"] == [
        {"room": ROOM, "orig": "$sent1", "new_body": t.STREAM_SILENT_MARKER}
    ]


@pytest.mark.asyncio
async def test_finalise_silent_no_placeholder_no_error_is_noop(trigger):
    """No placeholder shipped AND no error → nothing to clean up. Locks
    that we don't spuriously send a silent marker when the room was
    already clean."""
    t, calls = trigger
    state = _StreamReplyState()
    await t._finalize_stream_silent(_cred(), ROOM, state)
    assert calls["redact"] == []
    assert calls["edit"] == []
    assert calls["send"] == []


# ── finalise: error ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_finalise_error_edits_placeholder_to_error_marker(trigger):
    """When the runtime emitted an ERROR event during the stream
    (state.error_seen=True), the placeholder MUST become the error
    marker — not the discreet silent marker, and NOT redacted. This is
    the direct fix for the LineTooLong live incident: agent crashed
    → user saw only 'message deleted', now sees a 'try again' prompt."""
    t, calls = trigger
    state = _StreamReplyState(
        placeholder_event_id="$sent1",
        error_seen=True,
        last_error_message="LineTooLong: 400, Got more than 131072 bytes...",
    )
    await t._finalize_stream_silent(_cred(), ROOM, state)
    assert calls["redact"] == []
    assert calls["edit"] == [
        {"room": ROOM, "orig": "$sent1", "new_body": t.STREAM_ERROR_MARKER}
    ]


@pytest.mark.asyncio
async def test_finalise_error_no_placeholder_sends_fresh_marker(trigger):
    """Error but no placeholder ever shipped → send a FRESH error
    marker so the user still sees a 'try again' prompt. Contrast with
    the plain-silent no-placeholder case which correctly stays a
    no-op."""
    t, calls = trigger
    state = _StreamReplyState(error_seen=True)
    await t._finalize_stream_silent(_cred(), ROOM, state)
    assert calls["send"] == [{"room": ROOM, "body": t.STREAM_ERROR_MARKER}]
    assert calls["edit"] == []
    assert calls["redact"] == []


@pytest.mark.asyncio
async def test_error_event_sets_error_seen_flag(trigger):
    """MessageType.ERROR during the stream MUST flip the flag so
    finalize picks the error marker branch. Missing this coupling was
    the whole reason a LineTooLong crash rendered as a plain
    'message deleted' in the room."""
    t, _ = trigger
    state = _StreamReplyState(placeholder_event_id="$sent1")
    err_event = SimpleNamespace(
        message_type=MessageType.ERROR,
        error_type="LineTooLong",
        error_message="Got more than 131072 bytes ...",
    )
    await t._handle_stream_event(err_event, state, _cred(), ROOM)
    assert state.error_seen is True
    assert "LineTooLong" in state.last_error_message or \
        "Got more than" in state.last_error_message


# ── feature flag ────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_streaming_disabled_falls_back_to_atomic(trigger, monkeypatch):
    t, _ = trigger
    t.STREAMING_ENABLED = False
    atomic_called = AsyncMock(return_value="atomic-return")
    streaming_called = AsyncMock(return_value="streaming-return")
    monkeypatch.setattr(t, "_build_and_run_agent_atomic", atomic_called)
    monkeypatch.setattr(t, "_build_and_run_agent_streaming", streaming_called)
    msg = SimpleNamespace(
        chat_id=ROOM, message_id="$evt", sender_id="@u:h", content="hi"
    )
    result = await t._build_and_run_agent(_cred(), msg, "U", attachments=None)
    assert result == "atomic-return"
    atomic_called.assert_awaited_once()
    streaming_called.assert_not_awaited()
