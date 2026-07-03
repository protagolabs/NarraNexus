"""
@file_name: test_matrix_streaming_reply.py
@date: 2026-07-03
@description: MatrixTrigger progressive-streaming state machine tests.

Locks the design contract for the ``m.replace``-based reply streaming
(Plan X from the 2026-07-03 owner discussion):

- The placeholder DOES NOT ship until the agent generates at least
  ``STREAM_MIN_CHARS_BEFORE_PLACEHOLDER`` characters. Prevents an orphan
  "..." when the agent immediately decides not to reply.
- Edits are DEBOUNCED by both time (``STREAM_EDIT_DEBOUNCE_MS``) AND
  character delta (``STREAM_EDIT_MIN_DELTA_CHARS``) — either alone is
  insufficient to prevent Matrix rate limits under fast-token streams.
- ``narra_reply.text`` OVERWRITES any streamed thinking on finalise —
  this is why users never see the agent's raw AGENT_RESPONSE deltas as
  the "final" version.
- Silent-not-reply (no ``narra_reply`` tool call) REDACTS the placeholder
  if one was sent. If no placeholder ever shipped, nothing happens.
- Feature-flag off (``STREAMING_ENABLED=False``) falls back to the
  atomic path exactly as pre-streaming.
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


def _tool_call(tool_name: str, tool_input: dict):
    return SimpleNamespace(
        message_type=MessageType.TOOL_CALL,
        tool_name=tool_name,
        tool_input=tool_input,
    )


def _thinking(text: str):
    return SimpleNamespace(
        message_type=MessageType.AGENT_THINKING,
        thinking_content=text,
    )


@pytest.fixture
def trigger(monkeypatch):
    t = MatrixTrigger()
    # Shrink debounce so time-based logic doesn't gate away in tests.
    t.STREAM_EDIT_DEBOUNCE_MS = 0
    t.STREAM_EDIT_MIN_DELTA_CHARS = 5
    t.STREAM_MIN_CHARS_BEFORE_PLACEHOLDER = 3

    calls = {"send": [], "edit": [], "redact": []}

    async def _fake_send(*, homeserver, token, room_id, content, txn_id=None):
        calls["send"].append({"room": room_id, "body": content.get("body", "")})
        return f"$sent{len(calls['send'])}"

    async def _fake_edit(
        *, homeserver, token, room_id, original_event_id, new_body, txn_id=None
    ):
        calls["edit"].append({
            "room": room_id, "orig": original_event_id, "new_body": new_body,
        })
        return f"$edit{len(calls['edit'])}"

    async def _fake_redact(
        *, homeserver, token, room_id, event_id, reason="", txn_id=None
    ):
        calls["redact"].append({
            "room": room_id, "event_id": event_id, "reason": reason,
        })
        return f"$redact{len(calls['redact'])}"

    monkeypatch.setattr(mt_mod, "matrix_room_send", _fake_send)
    monkeypatch.setattr(mt_mod, "matrix_room_edit", _fake_edit)
    monkeypatch.setattr(mt_mod, "matrix_room_redact", _fake_redact)
    return t, calls


# ────────────────────────────────────────────────────────────────────
# Placeholder gating
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_placeholder_not_sent_below_min_chars(trigger):
    """If the agent generates fewer than MIN_CHARS_BEFORE_PLACEHOLDER
    (default 3 in the fixture), no placeholder ships. Guards against
    an "…" flash for turns the agent will silently drop."""
    t, calls = trigger
    state = _StreamReplyState()
    await t._handle_stream_event(_delta("hi"), state, _cred(), ROOM)
    assert calls["send"] == []
    assert state.placeholder_event_id == ""


@pytest.mark.asyncio
async def test_placeholder_shipped_at_min_chars(trigger):
    """At exactly MIN_CHARS_BEFORE_PLACEHOLDER, a placeholder ships
    with the ACCUMULATED text as the body (not the static "…") — so
    the room shows the agent's real first words immediately."""
    t, calls = trigger
    state = _StreamReplyState()
    await t._handle_stream_event(_delta("Hello"), state, _cred(), ROOM)
    assert len(calls["send"]) == 1
    assert calls["send"][0]["body"] == "Hello"
    assert state.placeholder_event_id == "$sent1"


# ────────────────────────────────────────────────────────────────────
# Edit debouncing
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_small_deltas_do_not_edit(trigger):
    """A dribble of 1-2 char deltas below MIN_DELTA_CHARS (5 in the
    fixture) should not fire an edit — otherwise a slow token stream
    burns edits on every single character."""
    t, calls = trigger
    state = _StreamReplyState()
    await t._handle_stream_event(_delta("Hello"), state, _cred(), ROOM)
    # placeholder is now out; last_edited_length == 5
    # Add a few 1-char deltas, still under the 5-char delta gate.
    for c in "! ":
        await t._handle_stream_event(_delta(c), state, _cred(), ROOM)
    assert calls["edit"] == []


@pytest.mark.asyncio
async def test_edit_fires_after_delta_and_debounce(trigger):
    """Once enough chars have arrived past MIN_DELTA_CHARS (and
    debounce time in the fixture is 0), the next AGENT_RESPONSE
    triggers an edit whose body is the FULL accumulated text — not
    just the incremental delta. Matrix edit protocol replaces, not
    appends."""
    t, calls = trigger
    state = _StreamReplyState()
    await t._handle_stream_event(_delta("Hello"), state, _cred(), ROOM)
    # placeholder shipped. Now push enough delta to cross the gate.
    await t._handle_stream_event(_delta(" world!"), state, _cred(), ROOM)
    assert len(calls["edit"]) == 1
    # The edit body is the FULL accumulated string.
    assert calls["edit"][0]["new_body"] == "Hello world!"
    assert calls["edit"][0]["orig"] == "$sent1"


# ────────────────────────────────────────────────────────────────────
# Thinking is ignored
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_agent_thinking_does_not_ship(trigger):
    """AGENT_THINKING events must never influence the placeholder or
    edit stream — they're internal reasoning. Streaming them would
    leak the chain-of-thought to the user."""
    t, calls = trigger
    state = _StreamReplyState()
    await t._handle_stream_event(_thinking("let me think..."), state, _cred(), ROOM)
    await t._handle_stream_event(_thinking("more thinking..."), state, _cred(), ROOM)
    assert calls["send"] == []
    assert calls["edit"] == []
    assert state.accumulated_text == ""


# ────────────────────────────────────────────────────────────────────
# Tool-call capture
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_narra_reply_captured(trigger):
    """narra_reply tool call captures its text arg into state; other
    tool calls are ignored (they aren't user-facing text)."""
    t, _ = trigger
    state = _StreamReplyState()
    await t._handle_stream_event(
        _tool_call("web_search", {"query": "irrelevant"}), state, _cred(), ROOM,
    )
    assert state.narra_reply_text == ""
    await t._handle_stream_event(
        _tool_call("narra_reply", {"text": "Final answer."}), state, _cred(), ROOM,
    )
    assert state.narra_reply_text == "Final answer."


@pytest.mark.asyncio
async def test_narra_reply_last_wins(trigger):
    """If the agent calls narra_reply twice in one turn (rare), the
    second call's text is what we commit — last-writer wins."""
    t, _ = trigger
    state = _StreamReplyState()
    await t._handle_stream_event(
        _tool_call("narra_reply", {"text": "first draft"}), state, _cred(), ROOM,
    )
    await t._handle_stream_event(
        _tool_call("narra_reply", {"text": "revised final"}), state, _cred(), ROOM,
    )
    assert state.narra_reply_text == "revised final"


# ────────────────────────────────────────────────────────────────────
# Finalise: with reply
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_finalise_with_reply_edits_placeholder(trigger):
    """When narra_reply.text is available AND a placeholder was
    shipped, finalise edits the placeholder with the final text —
    OVERWRITING any streamed thinking or partial answer. This is the
    contract that lets us stream loose text safely."""
    t, calls = trigger
    state = _StreamReplyState(
        placeholder_event_id="$sent1",
        accumulated_text="hmm, thinking...",
    )
    await t._finalize_stream_with_reply(
        _cred(), ROOM, state, "Clean final answer."
    )
    assert calls["edit"] == [{
        "room": ROOM,
        "orig": "$sent1",
        "new_body": "Clean final answer.",
    }]


@pytest.mark.asyncio
async def test_finalise_with_reply_no_placeholder_sends_fresh(
    trigger, monkeypatch
):
    """If the agent replied so fast that no placeholder ever shipped
    (streaming didn't cross MIN_CHARS_BEFORE_PLACEHOLDER), finalise
    falls back to _send_matrix_reply — the retry-aware atomic path."""
    t, calls = trigger
    state = _StreamReplyState()  # no placeholder_event_id
    fake_send = AsyncMock(return_value=True)
    monkeypatch.setattr(t, "_send_matrix_reply", fake_send)
    await t._finalize_stream_with_reply(
        _cred(), ROOM, state, "Instant answer."
    )
    fake_send.assert_awaited_once_with(_cred_matches_room_and_text(ROOM, "Instant answer.")) if False else None
    # Assertion simplified for clarity — verify the call happened.
    assert fake_send.await_count == 1
    args = fake_send.await_args
    # _send_matrix_reply(credential, room_id, content) — check positional order.
    assert args.args[1] == ROOM
    assert args.args[2] == "Instant answer."


def _cred_matches_room_and_text(*_a, **_kw):  # unused helper placeholder
    return None


# ────────────────────────────────────────────────────────────────────
# Finalise: silent
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_finalise_silent_redacts_placeholder(trigger):
    """No narra_reply + placeholder was sent → redact so the room
    doesn't retain a partial thinking snippet the agent then
    withdrew. This is the "silent-not-reply" invariant applied to
    the streaming path."""
    t, calls = trigger
    state = _StreamReplyState(placeholder_event_id="$sent1")
    await t._finalize_stream_silent(_cred(), ROOM, state)
    assert calls["redact"] == [{
        "room": ROOM,
        "event_id": "$sent1",
        "reason": "agent chose silent reply",
    }]


@pytest.mark.asyncio
async def test_finalise_silent_no_placeholder_is_noop(trigger):
    """No narra_reply AND no placeholder ever shipped → nothing to
    clean up. Purest silent case."""
    t, calls = trigger
    state = _StreamReplyState()
    await t._finalize_stream_silent(_cred(), ROOM, state)
    assert calls["redact"] == []
    assert calls["edit"] == []
    assert calls["send"] == []


# ────────────────────────────────────────────────────────────────────
# Feature flag
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_streaming_disabled_falls_back_to_atomic(trigger, monkeypatch):
    """STREAMING_ENABLED=False must NOT reach the streaming state
    machine at all — it hands off to _build_and_run_agent_atomic
    unchanged. This is the kill switch for a Matrix rate-limit spike
    or a debug session where we want the pre-streaming behaviour."""
    t, _ = trigger
    t.STREAMING_ENABLED = False
    atomic_called = AsyncMock(return_value="atomic-return")
    streaming_called = AsyncMock(return_value="streaming-return")
    monkeypatch.setattr(t, "_build_and_run_agent_atomic", atomic_called)
    monkeypatch.setattr(t, "_build_and_run_agent_streaming", streaming_called)
    msg = SimpleNamespace(chat_id=ROOM, message_id="$evt", sender_id="@u:h", content="hi")
    result = await t._build_and_run_agent(_cred(), msg, "U", attachments=None)
    assert result == "atomic-return"
    atomic_called.assert_awaited_once()
    streaming_called.assert_not_awaited()
