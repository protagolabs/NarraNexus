"""
@file_name: test_voice_reply_binding.py
@date: 2026-11-19
@description: Voice contract v15 — every reply to a voice turn carries the
``ai.netmind.rtc.voice_reply`` binding.

Locks:
- The binding is built only from a strictly-validated turn (both ids
  non-blank); a degraded turn or a text turn yields none.
- Every outlet that answers a voice turn stamps it: the live bridge (base
  event AND both levels of each ``m.replace``), the plain retry-aware
  sender (bridge fallback / ``narra_reply`` fresh-send / circuit refusal),
  the error marker, and the platform no-reply fallback
  (``reply_kwargs`` -> ``send_to_agent``).
- A text turn's events are byte-identical to before (no binding key).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from nio import RoomSendResponse

from narranexus.platform.schema.runtime_message import MessageType
from narranexus_plugins.narramessenger_module import matrix_trigger as mt_mod
from narranexus_plugins.narramessenger_module import (
    narramessenger_module as mod_mod,
)
from narranexus_plugins.narramessenger_module._narramessenger_credential_manager import (
    NarramessengerCredential,
)
from narranexus_plugins.narramessenger_module._rtc_voice import (
    RTC_VOICE_REPLY_KEY,
    voice_reply_binding,
    with_voice_reply_binding,
)
from narranexus_plugins.narramessenger_module.matrix_trigger import (
    MatrixTrigger,
)
from narranexus_plugins.narramessenger_module.narramessenger_context_builder import (
    NarramessengerContextBuilder,
)
from narranexus_plugins.narramessenger_module.narramessenger_module import (
    NarramessengerModule,
)

ROOM = "!room:matrix.netmind.chat"
SESSION = "11111111-1111-4111-8111-111111111111"
TURN = "22222222-2222-4222-8222-222222222222"
BINDING = {"rtc_session_id": SESSION, "turn_id": TURN}
SPEAK = "mcp__narramessenger_module__speak"


def _cred() -> NarramessengerCredential:
    return NarramessengerCredential(
        agent_id="agent_x",
        bearer_token="narra-tok",
        backend_base_url="https://api.netmind.chat",
        matrix_homeserver_url="https://matrix.netmind.chat",
        matrix_user_id="@agent-x:matrix.netmind.chat",
        matrix_access_token="syt_x",
    )


def _rtc(*, degraded: bool = False) -> dict:
    return {
        "rtc_session_id": "" if degraded else SESSION,
        "turn_id": "" if degraded else TURN,
        "invocation_id": "" if degraded else "inv",
        "agent_profile_id": "" if degraded else "prof",
        "voice_instructions": "Be brief.",
        "degraded": degraded,
    }


def _msg(raw: dict):
    return SimpleNamespace(
        chat_id=ROOM,
        message_id="$evt",
        sender_id="@u:h",
        content="hi",
        raw=raw,
        timestamp_ms=0,
        sender_name="U",
    )


# ── pure helpers ────────────────────────────────────────────────────────
def test_binding_built_from_strict_turn_only():
    assert voice_reply_binding(_rtc()) == BINDING
    assert voice_reply_binding(_rtc(degraded=True)) is None
    assert voice_reply_binding(None) is None
    assert voice_reply_binding({}) is None
    assert voice_reply_binding({"rtc_session_id": SESSION, "turn_id": "  "}) is None
    assert voice_reply_binding({"rtc_session_id": 7, "turn_id": TURN}) is None


def test_with_binding_stamps_both_levels_without_mutating_input():
    base = {"msgtype": "m.text", "body": "hi"}
    assert with_voice_reply_binding(base, BINDING)[RTC_VOICE_REPLY_KEY] == BINDING
    assert RTC_VOICE_REPLY_KEY not in base

    edit = {
        "msgtype": "m.text",
        "body": "* hi there",
        "m.new_content": {"msgtype": "m.text", "body": "hi there"},
        "m.relates_to": {"rel_type": "m.replace", "event_id": "$e1"},
    }
    stamped = with_voice_reply_binding(edit, BINDING)
    assert stamped[RTC_VOICE_REPLY_KEY] == BINDING
    assert stamped["m.new_content"][RTC_VOICE_REPLY_KEY] == BINDING
    assert RTC_VOICE_REPLY_KEY not in edit["m.new_content"]


def test_with_binding_none_is_identity():
    content = {"msgtype": "m.text", "body": "hi"}
    assert with_voice_reply_binding(content, None) == content


# ── streaming path end to end ───────────────────────────────────────────
@pytest.fixture
def stream_trigger(monkeypatch):
    """A MatrixTrigger wired to a fake runtime stream and a capturing
    homeserver sender. ``events`` is the stream the fake runtime yields;
    ``sent`` collects every full content dict that reached ``room_send``;
    ``run_kwargs`` captures what the trigger handed to ``run_stream``."""
    t = MatrixTrigger()
    sent: list[dict] = []
    events: list = []
    run_kwargs: dict = {}

    async def _fake_send(*, homeserver, token, room_id, content, txn_id=None):
        sent.append(content)
        return f"$sent{len(sent)}"

    monkeypatch.setattr(mt_mod, "matrix_room_send", _fake_send)

    class _FakeBuilder:
        def __init__(self, message):
            self._message = message

        async def build_prompt(self, _):
            return "prompt"

        async def build_retrieval_anchor(self):
            return None

        def turn_envelope(self):
            # The real builder's envelope, so the streaming tests exercise
            # the production reply_kwargs seam instead of the base's
            # "envelope failed" fallback.
            real = NarramessengerContextBuilder(self._message, _cred(), "agent_x")
            return {
                "channel_room_type": "direct",
                "channel_reply_kwargs": real.reply_kwargs(),
            }

    monkeypatch.setattr(
        t, "create_context_builder", lambda message, *a, **kw: _FakeBuilder(message)
    )
    monkeypatch.setattr(t, "_resolve_agent_owner", AsyncMock(return_value="owner"))

    async def _stream():
        for event in events:
            if isinstance(event, Exception):
                raise event
            yield event

    class _FakeRuntimeClient:
        def run_stream(self, **kw):
            run_kwargs.update(kw)
            return _stream()

    monkeypatch.setattr(
        "narranexus.platform.agent_runtime.client.get_agent_runtime_client",
        lambda: _FakeRuntimeClient(),
    )
    return t, sent, events, run_kwargs


def _speak_delta(text: str):
    return SimpleNamespace(
        message_type=MessageType.AGENT_REPLY_DELTA,
        tool_name=SPEAK,
        call_id="c1",
        delta=text,
    )


@pytest.mark.asyncio
async def test_live_reply_base_and_edits_all_carry_binding(stream_trigger):
    t, sent, events, _ = stream_trigger
    events.extend([_speak_delta("It will rain. "), _speak_delta("Take an umbrella.")])

    spoken = await t._build_and_run_agent_streaming(
        _cred(), _msg({"rtc_voice": _rtc()}), "U", attachments=None
    )

    assert spoken == "It will rain. Take an umbrella."
    assert len(sent) >= 2, "expected a base event plus at least the final edit"
    for content in sent:
        assert content[RTC_VOICE_REPLY_KEY] == BINDING
        if "m.new_content" in content:
            assert content["m.new_content"][RTC_VOICE_REPLY_KEY] == BINDING
    assert "m.new_content" in sent[-1]


@pytest.mark.asyncio
async def test_binding_reaches_the_platform_fallback_envelope(stream_trigger):
    t, _, events, run_kwargs = stream_trigger
    events.append(_speak_delta("Hello there."))

    await t._build_and_run_agent_streaming(
        _cred(), _msg({"rtc_voice": _rtc()}), "U", attachments=None
    )
    extra = run_kwargs["trigger_extra_data"]
    assert extra["channel_reply_kwargs"] == {"voice_reply": BINDING}

    run_kwargs.clear()
    await t._build_and_run_agent_streaming(_cred(), _msg({}), "U", attachments=None)
    assert run_kwargs["trigger_extra_data"]["channel_reply_kwargs"] == {}


@pytest.mark.asyncio
async def test_merged_batch_is_bound_to_the_newest_turn(stream_trigger, monkeypatch):
    """Utterances buffered while a run is active merge into one turn; its
    reply must be bound to the NEWEST turn id — the one Narra considers
    current — never to a superseded one."""
    from dataclasses import dataclass

    t, sent, events, _ = stream_trigger

    @dataclass
    class _M:
        chat_id: str
        message_id: str
        sender_id: str
        content: str
        raw: dict
        timestamp_ms: int = 0

    def _turn(turn_id: str, text: str) -> _M:
        rtc = {**_rtc(), "turn_id": turn_id}
        return _M(ROOM, f"$e-{turn_id}", "@u:h", text, {"rtc_voice": rtc})

    first, second, third = _turn("T1", "one"), _turn("T2", "two"), _turn("T3", "three")

    real_stream = t._build_and_run_agent_streaming
    calls = {"n": 0}

    async def _stream_then_queue(cred, message, sender, *, attachments=None):
        calls["n"] += 1
        if calls["n"] == 1:
            # Two more utterances arrive while the first run is active.
            await t._run_voice_serialized(cred, second, "U")
            await t._run_voice_serialized(cred, third, "U")
        events.clear()
        events.append(_speak_delta("Answer."))
        return await real_stream(cred, message, sender, attachments=attachments)

    monkeypatch.setattr(t, "_build_and_run_agent_streaming", _stream_then_queue)
    await t._run_voice_serialized(_cred(), first, "U")

    assert calls["n"] == 2
    bound_turns = [c[RTC_VOICE_REPLY_KEY]["turn_id"] for c in sent]
    assert set(bound_turns) == {"T1", "T3"}
    assert bound_turns[-1] == "T3"
    assert "T2" not in bound_turns


@pytest.mark.asyncio
async def test_degraded_voice_turn_sends_no_binding(stream_trigger):
    t, sent, events, _ = stream_trigger
    events.append(_speak_delta("Hello there."))

    await t._build_and_run_agent_streaming(
        _cred(), _msg({"rtc_voice": _rtc(degraded=True)}), "U", attachments=None
    )

    assert sent
    for content in sent:
        assert RTC_VOICE_REPLY_KEY not in content
        assert RTC_VOICE_REPLY_KEY not in content.get("m.new_content", {})


@pytest.mark.asyncio
async def test_error_marker_on_voice_turn_carries_binding(stream_trigger):
    t, sent, events, _ = stream_trigger
    events.append(RuntimeError("transport crash"))

    await t._build_and_run_agent_streaming(
        _cred(), _msg({"rtc_voice": _rtc()}), "U", attachments=None
    )

    assert [c["body"] for c in sent] == [t.STREAM_ERROR_MARKER]
    assert sent[0][RTC_VOICE_REPLY_KEY] == BINDING


@pytest.mark.asyncio
async def test_error_marker_on_text_turn_is_unchanged(stream_trigger):
    t, sent, events, _ = stream_trigger
    events.append(RuntimeError("transport crash"))

    await t._build_and_run_agent_streaming(
        _cred(), _msg({}), "U", attachments=None
    )

    assert sent == [{"msgtype": "m.text", "body": t.STREAM_ERROR_MARKER}]


@pytest.mark.asyncio
async def test_bridge_failure_plain_fallback_carries_binding(
    stream_trigger, monkeypatch
):
    """Live lifecycle breaks on every homeserver send -> the answer goes
    out through the retry-aware plain sender, which must stay bound (an
    unbound reply after an acked call.started is dropped by Narra)."""
    t, _, events, _ = stream_trigger
    events.append(_speak_delta("It will rain."))

    async def _broken_send(**kw):
        raise mt_mod.MatrixSendError("http_500", "boom")

    monkeypatch.setattr(mt_mod, "matrix_room_send", _broken_send)
    plain = AsyncMock(return_value=True)
    monkeypatch.setattr(t, "_send_matrix_reply", plain)

    await t._build_and_run_agent_streaming(
        _cred(), _msg({"rtc_voice": _rtc()}), "U", attachments=None
    )

    plain.assert_awaited_once()
    assert plain.await_args.kwargs["voice_reply"] == BINDING


@pytest.mark.asyncio
async def test_narra_reply_fresh_send_on_voice_turn_carries_binding(
    stream_trigger, monkeypatch
):
    """Spoken form sanitizes to nothing (URL-only reply) -> the legacy
    capture is fresh-sent; on a voice turn that send is bound too."""
    t, _, events, _ = stream_trigger
    events.append(
        SimpleNamespace(
            message_type=MessageType.PROGRESS,
            details={
                "tool_name": "mcp__narramessenger_module__narra_reply",
                "arguments": {"text": "https://example.com/report"},
            },
        )
    )
    plain = AsyncMock(return_value=True)
    monkeypatch.setattr(t, "_send_matrix_reply", plain)

    await t._build_and_run_agent_streaming(
        _cred(), _msg({"rtc_voice": _rtc()}), "U", attachments=None
    )

    plain.assert_awaited_once()
    assert plain.await_args.kwargs["voice_reply"] == BINDING


# ── plain sender / refusal ──────────────────────────────────────────────
def _trigger_with_client():
    t = MatrixTrigger()
    client = SimpleNamespace(
        room_send=AsyncMock(return_value=RoomSendResponse("$e", ROOM))
    )
    t._clients[t._subscriber_key(_cred())] = client
    return t, client


@pytest.mark.asyncio
async def test_plain_sender_stamps_binding_when_given():
    t, client = _trigger_with_client()

    assert await t._send_matrix_reply(_cred(), ROOM, "hi", voice_reply=BINDING)

    assert client.room_send.await_args.kwargs["content"] == {
        "msgtype": "m.text",
        "body": "hi",
        RTC_VOICE_REPLY_KEY: BINDING,
    }


@pytest.mark.asyncio
async def test_plain_sender_text_turn_content_is_unchanged():
    t, client = _trigger_with_client()

    assert await t._send_matrix_reply(_cred(), ROOM, "hi")

    assert client.room_send.await_args.kwargs["content"] == {
        "msgtype": "m.text",
        "body": "hi",
    }


@pytest.mark.asyncio
async def test_circuit_refusal_on_voice_turn_carries_binding():
    t, client = _trigger_with_client()

    await t._send_circuit_refusal(_cred(), _msg({"rtc_voice": _rtc()}), "busy")
    assert client.room_send.await_args.kwargs["content"][RTC_VOICE_REPLY_KEY] == BINDING

    await t._send_circuit_refusal(_cred(), _msg({}), "busy")
    assert RTC_VOICE_REPLY_KEY not in client.room_send.await_args.kwargs["content"]


# ── platform no-reply fallback (reply_kwargs -> send_to_agent) ──────────
def test_context_builder_reply_kwargs_carry_binding_for_voice_only():
    voice = NarramessengerContextBuilder(_msg({"rtc_voice": _rtc()}), _cred(), "agent_x")
    assert voice.reply_kwargs() == {"voice_reply": BINDING}

    degraded = NarramessengerContextBuilder(
        _msg({"rtc_voice": _rtc(degraded=True)}), _cred(), "agent_x"
    )
    assert degraded.reply_kwargs() == {}

    text = NarramessengerContextBuilder(_msg({}), _cred(), "agent_x")
    assert text.reply_kwargs() == {}


@pytest.mark.asyncio
async def test_send_to_agent_stamps_binding_from_reply_kwargs(monkeypatch):
    sent: list[dict] = []

    async def _fake_send(*, homeserver, token, room_id, content, txn_id=None):
        sent.append(content)
        return "$e1"

    monkeypatch.setattr(mod_mod, "matrix_room_send", _fake_send)
    module = NarramessengerModule.__new__(NarramessengerModule)
    monkeypatch.setattr(module, "get_credential", AsyncMock(return_value=_cred()))

    result = await module.send_to_agent("agent_x", ROOM, "fallback", voice_reply=BINDING)
    assert result["success"] is True
    assert sent[-1] == {
        "msgtype": "m.text",
        "body": "fallback",
        RTC_VOICE_REPLY_KEY: BINDING,
    }

    await module.send_to_agent("agent_x", ROOM, "plain")
    assert sent[-1] == {"msgtype": "m.text", "body": "plain"}


# ── a speak-only voice turn counts as answered ──────────────────────────
def _tool_frame(tool_name: str, text: str = "It will rain."):
    from narranexus.platform.schema.runtime_message import (
        ProgressMessage,
        ProgressStatus,
    )

    return ProgressMessage(
        step="3.4.1",
        title="Tool call",
        description=tool_name,
        status=ProgressStatus.COMPLETED,
        details={"tool_name": tool_name, "arguments": {"text": text}},
    )


def test_speak_only_voice_turn_does_not_arm_the_dm_fallback():
    """The voice prompt makes ``speak`` the only way to be heard. If the
    runtime does not count it as a reply, the DM fallback writes a second
    helper-authored answer — which now carries this turn's binding and can
    win the race against the agent's real one."""
    from narranexus.platform.agent_runtime._agent_runtime_steps.step_3_agent_loop import (
        _should_run_helper_llm_fallback,
    )

    def _decide(frame):
        return _should_run_helper_llm_fallback(
            working_source="narramessenger",
            agent_loop_response=[frame],
            cancellation=None,
            is_direct_message=True,
        )

    assert _decide(_tool_frame(SPEAK)) == (None, "already_replied_via_tool")
    # Control: a non-reply tool still arms it.
    assert _decide(_tool_frame("mcp__chat_module__get_chat_history")) == (
        "no_reply_im_dm",
        "",
    )


def test_reply_extractor_reads_speak_text_by_exact_leaf_name():
    extract = mod_mod._extract_narramessenger_reply
    assert extract(SPEAK, {"text": "It will rain."}) == "It will rain."
    assert extract("speak", {"text": "Hi."}) == "Hi."
    assert extract("mcp__other__loudspeaker", {"text": "x"}) is None
