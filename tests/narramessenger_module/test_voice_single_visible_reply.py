"""
@file_name: test_voice_single_visible_reply.py
@date: 2026-11-19
@description: Voice contract v15 — a turn has exactly one visible reply event.

Narra rejects a second NEW message bound to a turn that already has a
visible reply (``visible_event_already_bound``). The live bridge already
keeps to one base event plus edits; this locks the fallback: when the live
base went out but the final edit failed, the retry-aware fallback must
finish THAT event with an ``m.replace`` instead of sending a new message.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from nio import RoomSendResponse

from narranexus.platform.schema.runtime_message import MessageType
from narranexus_plugins.narramessenger_module import matrix_trigger as mt_mod
from narranexus_plugins.narramessenger_module._narramessenger_credential_manager import (
    NarramessengerCredential,
)
from narranexus_plugins.narramessenger_module.voice._rtc_voice import RTC_VOICE_REPLY_KEY
from narranexus_plugins.narramessenger_module.voice._voice_delivery import (
    LIVE_MARKER_KEY,
    VoiceDeliveryBridge,
    replacement_content,
)
from narranexus_plugins.narramessenger_module.matrix_trigger import MatrixTrigger

ROOM = "!room:matrix.netmind.chat"
BINDING = {"rtc_session_id": "S", "turn_id": "T"}
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


def _voice_msg():
    rtc = {
        "rtc_session_id": "S",
        "turn_id": "T",
        "invocation_id": "i",
        "agent_profile_id": "p",
        "voice_instructions": "Be brief.",
        "degraded": False,
    }
    return SimpleNamespace(
        chat_id=ROOM, message_id="$in", sender_id="@u:h", content="hi",
        raw={"rtc_voice": rtc}, timestamp_ms=0, sender_name="U",
    )


def test_replacement_content_shape():
    assert replacement_content("final text", "$base") == {
        "msgtype": "m.text",
        "body": "* final text",
        "m.new_content": {"msgtype": "m.text", "body": "final text"},
        "m.relates_to": {"rel_type": "m.replace", "event_id": "$base"},
    }


@pytest.mark.asyncio
async def test_bridge_exposes_the_base_event_id_once_sent():
    sent: list[dict] = []

    async def _send(content: dict) -> str:
        sent.append(content)
        return "$base"

    bridge = VoiceDeliveryBridge(send=_send, clock=lambda: 0.0)
    assert bridge.base_event_id is None
    await bridge.on_reply_delta(call_id="c1", delta="Hello there.")
    assert bridge.base_event_id == "$base"
    await bridge.close()
    assert sent[-1]["m.relates_to"]["event_id"] == "$base"


@pytest.mark.asyncio
async def test_plain_sender_edits_the_given_event_bound_on_both_levels():
    t = MatrixTrigger()
    client = SimpleNamespace(
        room_send=AsyncMock(return_value=RoomSendResponse("$e", ROOM))
    )
    t._clients[t._subscriber_key(_cred())] = client

    assert await t._send_matrix_reply(
        _cred(), ROOM, "final text", voice_reply=BINDING, replaces_event_id="$base"
    )

    content = client.room_send.await_args.kwargs["content"]
    assert content["m.relates_to"] == {"rel_type": "m.replace", "event_id": "$base"}
    assert content["m.new_content"]["body"] == "final text"
    assert content[RTC_VOICE_REPLY_KEY] == BINDING
    assert content["m.new_content"][RTC_VOICE_REPLY_KEY] == BINDING
    # The fallback is the turn's FINAL state: no live marker on either level.
    assert LIVE_MARKER_KEY not in content
    assert LIVE_MARKER_KEY not in content["m.new_content"]


async def _run_turn(monkeypatch, *, fail_from_send: int):
    """Drive one voice turn whose homeserver sends start failing at the
    ``fail_from_send``-th call (1-based). Returns the fallback sender mock."""
    t = MatrixTrigger()
    calls = {"n": 0}

    async def _room_send(*, homeserver, token, room_id, content, txn_id=None):
        calls["n"] += 1
        if calls["n"] >= fail_from_send:
            raise mt_mod.MatrixSendError("http_500", "boom")
        return "$base"

    monkeypatch.setattr(mt_mod, "matrix_room_send", _room_send)

    class _Builder:
        async def build_prompt(self, _):
            return "prompt"

        async def build_retrieval_anchor(self):
            return None

        def turn_envelope(self):
            return {}

    monkeypatch.setattr(t, "create_context_builder", lambda *a, **k: _Builder())
    monkeypatch.setattr(t, "_resolve_agent_owner", AsyncMock(return_value="owner"))

    async def _stream():
        for text in ("It will rain. ", "Take an umbrella."):
            yield SimpleNamespace(
                message_type=MessageType.AGENT_REPLY_DELTA,
                tool_name=SPEAK,
                call_id="c1",
                delta=text,
            )

    monkeypatch.setattr(
        "narranexus.platform.agent_runtime.client.get_agent_runtime_client",
        lambda: SimpleNamespace(run_stream=lambda **kw: _stream()),
    )
    plain = AsyncMock(return_value=True)
    monkeypatch.setattr(t, "_send_matrix_reply", plain)
    spoken = await t._build_and_run_agent_streaming(
        _cred(), _voice_msg(), "U", attachments=None
    )
    assert spoken == "It will rain. Take an umbrella."
    return plain


@pytest.mark.asyncio
async def test_fallback_after_the_base_went_out_edits_the_base(monkeypatch):
    plain = await _run_turn(monkeypatch, fail_from_send=2)
    plain.assert_awaited_once()
    assert plain.await_args.kwargs["replaces_event_id"] == "$base"
    assert plain.await_args.args[2] == "It will rain. Take an umbrella."


@pytest.mark.asyncio
async def test_fallback_when_nothing_reached_the_room_sends_a_new_message(monkeypatch):
    plain = await _run_turn(monkeypatch, fail_from_send=1)
    plain.assert_awaited_once()
    assert plain.await_args.kwargs["replaces_event_id"] is None
