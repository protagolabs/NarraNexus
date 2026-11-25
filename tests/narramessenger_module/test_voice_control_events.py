"""
@file_name: test_voice_control_events.py
@date: 2026-11-19
@description: Voice contract v15 — downlink control events, the durable turn
claim, and ``ack.v2``.

Locks:
- Pure layer (``_voice_call``): which payloads are control events, the exact
  ack shape (call-level acks have NO ``turn_id`` key), deterministic ack
  transaction ids, the registry's first-wins pinning and expiry.
- Trigger: a control event is acked only when every gate passes (not from
  ourselves / the appservice bot, recent, live voice enabled, an
  authoritative 1:1 room, Narra authorizes the caller, session pinned to its
  first room + sender). Everything else is dropped WITHOUT an ack.
- ``turn.cancelled`` tombstones the turn; a duplicate voice input is claimed
  once; ``turn.started`` is acked only inside a known v15 call.
- Control events never reach the base pipeline (never yielded by /sync).
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from nio import UnknownEvent

from narranexus.platform.channel.channel_audit_events import (
    EVENT_DEDUP_FAIL_OPEN,
    EVENT_INGRESS_DROPPED_DEDUP,
    EVENT_TRANSPORT_SEND_FAILED,
)
from narranexus.platform.channel.channel_dedup_store import ChannelDedupStore
from narranexus_plugins.narramessenger_module import _matrix_send as ms
from narranexus_plugins.narramessenger_module.voice import _voice_control as vc_mod
from narranexus_plugins.narramessenger_module import matrix_trigger as mt_mod
from narranexus_plugins.narramessenger_module._narramessenger_credential_manager import (
    NarramessengerCredential,
)
from narranexus_plugins.narramessenger_module.voice._voice_call import (
    EVENT_VOICE_CONTROL_DROPPED,
    EVENT_VOICE_CONTROL_HANDLED,
    KIND_CALL_ENDED,
    KIND_CALL_STARTED,
    KIND_TURN_CANCELLED,
    KIND_TURN_HINT,
    KIND_TURN_STARTED,
    VOICE_ACK_EVENT_TYPE,
    VoiceCallRegistry,
    VoiceControlEvent,
    VoiceRateLimiter,
    build_voice_ack,
    parse_voice_control_event,
    voice_ack_txn_id,
    voice_turn_claim_key,
)
from narranexus_plugins.narramessenger_module.matrix_trigger import (
    MatrixTrigger,
    _AuthorizeVerdict,
)

ROOM = "!dm:matrix.netmind.chat"
OTHER_ROOM = "!other:matrix.netmind.chat"
AGENT = "@agent-x:matrix.netmind.chat"
CALLER = "@caller:matrix.netmind.chat"
BOT = "@hybrid-im-bot:matrix.netmind.chat"
SESSION = "11111111-1111-4111-8111-111111111111"
TURN = "22222222-2222-4222-8222-222222222222"

T_CALL_STARTED = "ai.netmind.rtc.agent.call.started.v2"
T_TURN_HINT = "ai.netmind.rtc.agent.turn_hint.v2"
T_TURN_CANCELLED = "ai.netmind.rtc.agent.turn.cancelled.v2"
T_CALL_ENDED = "ai.netmind.rtc.agent.call.ended.v2"


def _cred() -> NarramessengerCredential:
    return NarramessengerCredential(
        agent_id="agent_x",
        bearer_token="narra-tok",
        backend_base_url="https://api.netmind.chat",
        matrix_homeserver_url="https://matrix.netmind.chat",
        matrix_user_id=AGENT,
        matrix_access_token="syt_x",
    )


def _event(
    event_type: str,
    content: dict | None = None,
    *,
    sender: str = CALLER,
    ts_ms: int | None = None,
) -> UnknownEvent:
    """A custom timeline event as nio parses it off /sync."""
    if content is None:
        content = {"rtc_session_id": SESSION, "sent_at": "2026-11-19T08:00:00.000Z"}
        if event_type in (T_TURN_HINT, T_TURN_CANCELLED):
            content["turn_id"] = TURN
    return UnknownEvent.from_dict(
        {
            "type": event_type,
            "sender": sender,
            "event_id": f"$ctl-{event_type}-{sender}",
            "origin_server_ts": int(time.time() * 1000) if ts_ms is None else ts_ms,
            "content": content,
        }
    )


# ════════════════════════════════════════════════════════════════════════
# Pure layer
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "event_type,kind,has_turn",
    [
        (T_CALL_STARTED, KIND_CALL_STARTED, False),
        (T_TURN_HINT, KIND_TURN_HINT, True),
        (T_TURN_CANCELLED, KIND_TURN_CANCELLED, True),
        (T_CALL_ENDED, KIND_CALL_ENDED, False),
    ],
)
def test_parse_recognises_the_four_control_events(event_type, kind, has_turn):
    content = {"rtc_session_id": SESSION, "turn_id": TURN, "sent_at": "x"}
    ctl = parse_voice_control_event(event_type, content)
    assert ctl == VoiceControlEvent(
        kind=kind, rtc_session_id=SESSION, turn_id=TURN if has_turn else None
    )


@pytest.mark.parametrize(
    "event_type,content",
    [
        ("m.room.message", {"rtc_session_id": SESSION}),
        ("ai.netmind.rtc.agent.ack.v2", {"rtc_session_id": SESSION}),
        ("ai.netmind.rtc.agent.turn.started.v2", {"rtc_session_id": SESSION, "turn_id": TURN}),
        (T_CALL_STARTED, None),
        (T_CALL_STARTED, {}),
        (T_CALL_STARTED, {"rtc_session_id": "   "}),
        (T_CALL_STARTED, {"rtc_session_id": 7}),
        (T_TURN_CANCELLED, {"rtc_session_id": SESSION}),
        (T_TURN_CANCELLED, {"rtc_session_id": SESSION, "turn_id": ""}),
        (T_TURN_HINT, {"rtc_session_id": SESSION, "turn_id": None}),
        (None, {"rtc_session_id": SESSION}),
    ],
)
def test_parse_rejects_other_types_and_malformed_payloads(event_type, content):
    assert parse_voice_control_event(event_type, content) is None


LONG_ID = "x" * 129


@pytest.mark.parametrize(
    "event_type,content",
    [
        (T_CALL_STARTED, {"rtc_session_id": LONG_ID}),
        (T_TURN_CANCELLED, {"rtc_session_id": SESSION, "turn_id": LONG_ID}),
        (T_TURN_HINT, {"rtc_session_id": LONG_ID, "turn_id": TURN}),
    ],
)
def test_parse_rejects_over_long_ids(event_type, content):
    """The sender chooses these ids and they are stored and audited: an
    over-long one is rejected, never truncated (keys are hashes of them)."""
    assert parse_voice_control_event(event_type, content) is None


def test_ids_at_the_cap_are_accepted():
    at_cap = "x" * 128
    ctl = parse_voice_control_event(
        T_TURN_CANCELLED, {"rtc_session_id": at_cap, "turn_id": at_cap}
    )
    assert ctl is not None and ctl.turn_id == at_cap


def test_message_path_rejects_over_long_ids_too():
    from narranexus_plugins.narramessenger_module.voice._rtc_voice import (
        parse_rtc_voice_input,
        voice_reply_binding,
    )

    assert voice_reply_binding({"rtc_session_id": SESSION, "turn_id": LONG_ID}) is None
    assert voice_reply_binding({"rtc_session_id": LONG_ID, "turn_id": TURN}) is None

    def _input(turn_id: str) -> dict:
        return {
            "type": "m.room.message",
            "content": {
                "msgtype": "m.text",
                "body": "hi",
                "ai.netmind.rtc.voice_input": {
                    "version": 1, "transport": "matrix", "seq": 1,
                    "transcript_final": True, "rtc_session_id": SESSION,
                    "turn_id": turn_id, "invocation_id": "i", "agent_profile_id": "p",
                },
            },
        }

    assert parse_rtc_voice_input(_input(TURN)) is not None
    # Strict parse fails -> the turn degrades to voice without correlation.
    assert parse_rtc_voice_input(_input(LONG_ID)) is None


def test_call_level_ack_has_no_turn_id_key():
    for kind in (KIND_CALL_STARTED, KIND_CALL_ENDED):
        assert build_voice_ack(kind, SESSION, TURN) == {
            "ack_of": kind,
            "rtc_session_id": SESSION,
            "platform": "nexus",
            "call_mode": "realtime",
            "supports_long_task_delivery": False,
        }


def test_turn_level_ack_carries_turn_id_and_requires_it():
    for kind in (KIND_TURN_STARTED, KIND_TURN_HINT, KIND_TURN_CANCELLED):
        ack = build_voice_ack(kind, SESSION, TURN)
        assert ack["turn_id"] == TURN and ack["ack_of"] == kind
        with pytest.raises(ValueError):
            build_voice_ack(kind, SESSION, None)


def test_ack_txn_id_is_stable_and_distinct_per_logical_ack():
    a = voice_ack_txn_id(KIND_TURN_CANCELLED, SESSION, TURN)
    assert a == voice_ack_txn_id(KIND_TURN_CANCELLED, SESSION, TURN)
    assert a != voice_ack_txn_id(KIND_TURN_HINT, SESSION, TURN)
    assert a != voice_ack_txn_id(KIND_TURN_CANCELLED, SESSION, "other-turn")
    assert a != voice_ack_txn_id(KIND_TURN_CANCELLED, "other-session", TURN)
    # Opaque ids of any length / charset still give a path-safe txn id.
    weird = voice_ack_txn_id(KIND_CALL_STARTED, "s/../?" * 80)
    assert weird.startswith("nx-ack-") and weird[7:].isalnum() and len(weird) < 64


def test_turn_claim_key_fits_the_dedup_column_and_separates_fields():
    key = voice_turn_claim_key(ROOM, SESSION, TURN)
    assert key == voice_turn_claim_key(ROOM, SESSION, TURN)
    # agent_id + ":" + key must stay under channel_seen_messages.message_id (128).
    long_key = voice_turn_claim_key("r" * 500, "s" * 500, "t" * 500)
    assert len("agent_0123456789abcdef:" + long_key) < 128
    # No ambiguity between adjacent fields.
    assert voice_turn_claim_key(ROOM, "ab", "c") != voice_turn_claim_key(ROOM, "a", "bc")
    # The room is part of the key: quoting a turn's ids in another room
    # cannot touch that turn.
    assert key != voice_turn_claim_key(OTHER_ROOM, SESSION, TURN)


def test_registry_pins_first_room_and_caller_and_expires():
    now = [1000.0]
    reg = VoiceCallRegistry(ttl_seconds=60, clock=lambda: now[0])
    assert reg.get("a1", SESSION) is None
    first = reg.learn("a1", SESSION, ROOM, CALLER)
    # First sight wins: a later learn returns the SAME entry, unchanged.
    again = reg.learn("a1", SESSION, OTHER_ROOM, "@mallory:h")
    assert again is first and (again.room_id, again.caller_mxid) == (ROOM, CALLER)
    assert reg.matches("a1", SESSION, ROOM, CALLER)
    assert not reg.matches("a1", SESSION, OTHER_ROOM, CALLER)
    assert not reg.matches("a1", SESSION, ROOM, "@mallory:h")
    assert not reg.matches("a2", SESSION, ROOM, CALLER)  # per agent
    now[0] += 61
    assert reg.get("a1", SESSION) is None and len(reg) == 0


def test_registry_expiry_forgets_the_call_but_never_kills_its_turns():
    """A call that outlives the TTL is still a live call. Expiry must only
    cost acks: its turns keep running, and its next control event teaches
    the registry the same session again."""
    now = [0.0]
    reg = VoiceCallRegistry(ttl_seconds=60, clock=lambda: now[0])
    reg.learn("a1", SESSION, ROOM, CALLER)
    reg.mark_cancelled("a1", SESSION, "t-old")
    now[0] = 61
    assert not reg.turn_is_dead("a1", ROOM, SESSION, TURN)
    assert not reg.is_retired("a1", ROOM, SESSION)
    relearned = reg.learn("a1", SESSION, ROOM, CALLER)
    assert relearned is not None and reg.matches("a1", SESSION, ROOM, CALLER)


def test_registry_keeps_one_current_call_per_room_and_bounded_history():
    """Bounded by rooms, not by how many session ids someone can invent."""
    reg = VoiceCallRegistry()
    for i in range(50):
        reg.learn("a1", f"S{i}", ROOM, CALLER)
    assert len(reg) == 1 and reg.matches("a1", "S49", ROOM, CALLER)
    assert reg.get("a1", "S0") is None
    room = reg._rooms[("a1", ROOM)]
    assert len(room.retired) == reg.MAX_RETIRED_PER_ROOM
    assert reg.is_retired("a1", ROOM, "S48") and not reg.is_retired("a1", ROOM, "S0")
    reg.learn("a1", "other", OTHER_ROOM, CALLER)  # another room is independent
    reg.learn("a2", "S49b", ROOM, CALLER)  # and so is another agent
    assert len(reg) == 3 and reg.matches("a1", "S49", ROOM, CALLER)


def test_registry_cancelled_turns_and_ended_calls_are_dead():
    reg = VoiceCallRegistry()
    # A room with no record at all (v14 agent, fresh restart): nothing is dead.
    assert not reg.turn_is_dead("a1", ROOM, SESSION, TURN)
    reg.learn("a1", SESSION, ROOM, CALLER)
    assert not reg.turn_is_dead("a1", ROOM, SESSION, TURN)
    assert reg.mark_cancelled("a1", SESSION, TURN) is True
    assert reg.turn_is_dead("a1", ROOM, SESSION, TURN)
    assert not reg.turn_is_dead("a1", ROOM, SESSION, "other-turn")
    # Another room quoting the same ids is not affected.
    assert not reg.turn_is_dead("a1", OTHER_ROOM, SESSION, TURN)
    reg.end("a1", SESSION)
    # Ended: every turn of the call is dead and nothing more is acked,
    # but the entry stays so queued turns can still be recognised.
    assert reg.turn_is_dead("a1", ROOM, SESSION, "other-turn")
    assert not reg.matches("a1", SESSION, ROOM, CALLER)
    assert reg.get("a1", SESSION) is not None
    assert reg.mark_cancelled("a1", "never-seen", TURN) is False  # no entry created
    assert len(reg) == 1


def test_registry_redial_keeps_the_previous_calls_turns_dead():
    """Hang up and redial within seconds: the room's queue may still hold a
    turn of the first call. Replacing the call must not bring it back, and a
    late event of the old call must not push the new call out."""
    reg = VoiceCallRegistry()
    reg.learn("a1", "A", ROOM, CALLER)
    reg.end("a1", "A")
    assert reg.turn_is_dead("a1", ROOM, "A", "T2")

    reg.learn("a1", "B", ROOM, CALLER)
    assert reg.turn_is_dead("a1", ROOM, "A", "T2")  # still dead after the redial
    assert not reg.turn_is_dead("a1", ROOM, "B", "T1")
    assert reg.is_retired("a1", ROOM, "A")

    assert reg.learn("a1", "A", ROOM, CALLER) is None  # cannot come back
    assert reg.matches("a1", "B", ROOM, CALLER)  # the live call is untouched

    # A session this room never saw (first turn racing its call.started) is
    # not dead just because the room has some other current call.
    assert not reg.turn_is_dead("a1", ROOM, "C-not-yet-learned", "T1")


def test_registry_caps_the_cancelled_set():
    reg = VoiceCallRegistry()
    reg.MAX_CANCELLED_TURNS = 3
    reg.learn("a1", SESSION, ROOM, CALLER)
    assert all(reg.mark_cancelled("a1", SESSION, f"t{i}") for i in range(3))
    assert reg.mark_cancelled("a1", SESSION, "t3") is False
    assert reg.mark_cancelled("a1", SESSION, "t0") is True  # already recorded
    assert len(reg.get("a1", SESSION).cancelled_turns) == 3


def test_rate_limiter_is_a_sliding_window_and_flags_the_first_denial():
    now = [0.0]
    lim = VoiceRateLimiter(limit=2, window_seconds=10, clock=lambda: now[0])
    assert lim.check("a1", ROOM) == lim.OK and lim.allow("a1", ROOM)
    assert lim.check("a1", ROOM) == lim.DENIED_FIRST
    assert lim.check("a1", ROOM) == lim.DENIED
    assert lim.allow("a1", OTHER_ROOM) and lim.allow("a2", ROOM)  # per key
    now[0] = 10.5
    assert lim.allow("a1", ROOM) and lim.allow("a1", ROOM)
    # A new flood in a new window is reported once again.
    assert lim.check("a1", ROOM) == lim.DENIED_FIRST


def test_rate_limiter_forgets_rooms_that_went_quiet():
    now = [0.0]
    lim = VoiceRateLimiter(limit=1, window_seconds=10, clock=lambda: now[0])
    lim.SWEEP_EVERY = 4
    for i in range(3):
        lim.check("a1", f"room{i}")
        lim.check("a1", f"room{i}")  # denied: leaves a denial stamp too
    assert len(lim._seen) == 3 and len(lim._denied_at) == 3
    now[0] = 30.0
    for _ in range(4):
        lim.check("a1", "busy")
    assert set(lim._seen) == {("a1", "busy")} and set(lim._denied_at) <= {("a1", "busy")}


def test_rate_limiter_keeps_the_denial_stamp_inside_its_window():
    """Cleaning up must not make an ongoing flood look like a new one."""
    now = [0.0]
    lim = VoiceRateLimiter(limit=1, window_seconds=10, clock=lambda: now[0])
    lim.SWEEP_EVERY = 1
    assert lim.check("a1", ROOM) == lim.OK
    assert lim.check("a1", ROOM) == lim.DENIED_FIRST
    now[0] = 5.0
    assert lim.check("a1", ROOM) == lim.DENIED


class _FakeResp:
    def __init__(self, status: int, json_data: dict):
        self.status = status
        self._json = json_data

    async def json(self):
        return self._json

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


@pytest.mark.asyncio
async def test_matrix_send_event_puts_to_the_custom_event_type(monkeypatch):
    rec: dict = {}

    class _Session:
        def put(self, url, headers=None, json=None):
            rec.update(url=url, headers=headers, json=json)
            return _FakeResp(200, {"event_id": "$ack"})

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(ms.aiohttp, "ClientSession", lambda timeout=None: _Session())
    content = build_voice_ack(KIND_CALL_STARTED, SESSION)
    event_id = await ms.matrix_send_event(
        homeserver="https://matrix.netmind.chat",
        token="syt_x",
        room_id=ROOM,
        event_type=VOICE_ACK_EVENT_TYPE,
        content=content,
        txn_id="t1",
    )
    assert event_id == "$ack"
    assert rec["url"] == (
        f"https://matrix.netmind.chat/_matrix/client/v3/rooms/{ROOM}"
        f"/send/ai.netmind.rtc.agent.ack.v2/t1"
    )
    assert rec["json"] == content  # the PUT body IS the content
    assert rec["headers"]["Authorization"] == "Bearer syt_x"


@pytest.mark.asyncio
async def test_matrix_send_event_requires_an_event_type():
    with pytest.raises(ms.MatrixSendError) as exc:
        await ms.matrix_send_event(
            homeserver="https://h", token="t", room_id=ROOM, event_type="", content={}
        )
    assert exc.value.code == "bad_event_type"


# ════════════════════════════════════════════════════════════════════════
# Trigger
# ════════════════════════════════════════════════════════════════════════
class _Harness:
    """A MatrixTrigger with the homeserver, the room shape and Narra's
    authorize-event replaced by controllable fakes."""

    def __init__(self, monkeypatch):
        self.t = MatrixTrigger()
        self.t._dedup_store = ChannelDedupStore("narramessenger")
        self.acks: list[dict] = []
        self.send_error: Exception | None = None
        self.shape = ("dm", 2)
        self.allow = True
        self.audits: list[tuple[str, dict]] = []

        self.send_gate: asyncio.Event | None = None
        self.send_timeouts: list = []

        async def _fake_send(
            *, homeserver, token, room_id, event_type, content, txn_id=None,
            timeout_seconds=None,
        ):
            self.send_timeouts.append(timeout_seconds)
            if self.send_gate is not None:
                await self.send_gate.wait()
            if self.send_error is not None:
                raise self.send_error
            self.acks.append(
                {"room": room_id, "type": event_type, "content": content, "txn": txn_id}
            )
            return "$ack"

        monkeypatch.setattr(vc_mod, "matrix_send_event", _fake_send)

        async def _shape(room_id, credential):
            return self.shape

        self.authorize = AsyncMock(side_effect=lambda *a, **k: _AuthorizeVerdict(allow=self.allow))
        monkeypatch.setattr(self.t, "_resolve_room_shape", _shape)
        monkeypatch.setattr(self.t, "_authorize_event", self.authorize)

        async def _audit(event_type, **kw):
            self.audits.append((event_type, kw))

        monkeypatch.setattr(self.t, "_audit", _audit)
        monkeypatch.setattr(self.t, "VOICE_ACK_RETRY_DELAY_SECONDS", 0)

    async def control(self, event, *, room: str = ROOM) -> None:
        """Run one control event through the handler and let its ack finish."""
        await self.t._handle_voice_control_event(_cred(), room, event)
        await self.settle()

    async def settle(self) -> None:
        """Let spawned ack tasks finish."""
        while self.t._voice_control_tasks:
            await asyncio.gather(*list(self.t._voice_control_tasks))

    def known(self) -> bool:
        return self.t._voice_registry.matches("agent_x", SESSION, ROOM, CALLER)

    def audit_types(self) -> list[str]:
        return [event_type for event_type, _ in self.audits]

    def drop_reasons(self) -> list[str]:
        return [
            kw["details"]["reason"]
            for event_type, kw in self.audits
            if event_type == EVENT_VOICE_CONTROL_DROPPED
        ]


@pytest.fixture
def h(monkeypatch):
    return _Harness(monkeypatch)


def _voice_msg(*, turn: str = TURN, session: str = SESSION, event_id: str = "$in1",
               room: str = ROOM, sender: str = CALLER, degraded: bool = False):
    rtc = {
        "rtc_session_id": "" if degraded else session,
        "turn_id": "" if degraded else turn,
        "invocation_id": "i",
        "agent_profile_id": "p",
        "voice_instructions": "Be brief.",
        "degraded": degraded,
    }
    return SimpleNamespace(
        chat_id=room, message_id=event_id, sender_id=sender, content="hi",
        raw={"rtc_voice": rtc}, timestamp_ms=0,
    )


@pytest.mark.asyncio
async def test_call_started_is_acked_and_the_call_is_learned(h):
    await h.control(_event(T_CALL_STARTED))

    assert h.acks == [
        {
            "room": ROOM,
            "type": VOICE_ACK_EVENT_TYPE,
            "content": {
                "ack_of": "call.started",
                "rtc_session_id": SESSION,
                "platform": "nexus",
                "call_mode": "realtime",
                "supports_long_task_delivery": False,
            },
            "txn": voice_ack_txn_id(KIND_CALL_STARTED, SESSION),
        }
    ]
    assert h.known()
    # The caller was authorized against this room, as a non-mention.
    origin = h.authorize.await_args.args[1]
    assert (origin.chat_id, origin.sender_id) == (ROOM, CALLER)
    assert h.authorize.await_args.kwargs == {"mentioned": False}
    event_type, kw = h.audits[-1]
    assert event_type == EVENT_VOICE_CONTROL_HANDLED
    assert kw["details"] == {
        "kind": "call.started",
        "rtc_session_id": SESSION,
        "turn_id": "",
    }
    # An ack is only useful while fresh: it does not get the 60 s default.
    assert h.send_timeouts == [h.t.VOICE_ACK_TIMEOUT_SECONDS]


@pytest.mark.asyncio
@pytest.mark.parametrize("sender", [AGENT, BOT, ""])
async def test_control_event_from_ourselves_or_the_service_bot_is_ignored(h, sender):
    await h.control(_event(T_CALL_STARTED, sender=sender))
    assert h.acks == [] and not h.known()
    h.authorize.assert_not_awaited()
    assert h.drop_reasons() == ["non_caller_sender"]


@pytest.mark.asyncio
async def test_stale_control_event_is_ignored(h):
    old = int(time.time() * 1000) - h.t.VOICE_CONTROL_MAX_AGE_MS - 1000
    await h.control(_event(T_CALL_STARTED, ts_ms=old))
    assert h.acks == [] and not h.known()
    h.authorize.assert_not_awaited()
    assert h.drop_reasons() == ["stale"]
    # The age is in the row: skewed clocks show up as an absurd value.
    age_ms = h.audits[-1][1]["details"]["age_ms"]
    assert h.t.VOICE_CONTROL_MAX_AGE_MS < age_ms < h.t.VOICE_CONTROL_MAX_AGE_MS + 60_000


@pytest.mark.asyncio
async def test_over_long_ids_never_reach_the_registry_or_the_audit_row(h):
    await h.control(_event(T_CALL_STARTED, {"rtc_session_id": LONG_ID}))
    await h.control(_event(T_CALL_STARTED))
    await h.control(_event(T_TURN_CANCELLED, {"rtc_session_id": SESSION, "turn_id": LONG_ID}))

    assert h.drop_reasons() == ["malformed", "malformed"]
    assert len(h.t._voice_registry) == 1
    assert h.t._voice_registry.get("agent_x", SESSION).cancelled_turns == set()
    assert LONG_ID not in repr(h.audits)
    h.authorize.assert_awaited_once()  # only the well-formed call.started


@pytest.mark.asyncio
async def test_replayed_backlog_does_not_spend_the_live_calls_budget(h):
    """After a long disconnect the room's backlog of control events is
    replayed at once — all stale. Those, and malformed / foreign events,
    must be rejected BEFORE the room's event budget, or the call in progress
    would find its call.started rate-limited."""
    h.t._voice_events = VoiceRateLimiter(limit=2)
    old = int(time.time() * 1000) - h.t.VOICE_CONTROL_MAX_AGE_MS - 1000
    for i in range(20):
        await h.control(
            _event(T_TURN_HINT, {"rtc_session_id": f"old{i}", "turn_id": TURN}, ts_ms=old)
        )
    for _ in range(20):
        await h.control(_event(T_TURN_CANCELLED, {"rtc_session_id": SESSION}))  # malformed
        await h.control(_event(T_CALL_STARTED, sender=AGENT))  # not the caller

    await h.control(_event(T_CALL_STARTED))  # the live call

    assert [a["content"]["ack_of"] for a in h.acks] == ["call.started"] and h.known()
    assert "room_rate_limited" not in h.drop_reasons()
    # The garbage is still bounded where it matters: audit rows.
    assert len(h.drop_reasons()) == h.t.VOICE_CONTROL_DROP_AUDITS_PER_MINUTE


@pytest.mark.asyncio
async def test_rate_limit_rows_survive_a_spent_drop_budget(h):
    """A flood first uses up the room's drop-audit budget; the one row that
    says "this room is now rate limited" must still be written."""
    h.t._voice_events = VoiceRateLimiter(limit=1)
    h.t._voice_drop_audits = VoiceRateLimiter(limit=2)
    for _ in range(5):
        await h.control(_event(T_CALL_STARTED, sender=AGENT))
    assert h.drop_reasons() == ["non_caller_sender"] * 2  # budget spent

    await h.control(_event(T_CALL_STARTED))
    for i in range(4):
        await h.control(_event(T_TURN_HINT, {"rtc_session_id": SESSION, "turn_id": f"t{i}"}))
    assert h.drop_reasons()[2:] == ["room_rate_limited"]


@pytest.mark.asyncio
async def test_audit_rows_fit_the_sender_column(h):
    """channel_trigger_audit.sender_id is VARCHAR(128) on MySQL: a longer
    value loses the whole row there (SQLite would never show it)."""
    long_sender = "@" + "s" * 300 + ":matrix.netmind.chat"
    await h.control(_event(T_CALL_STARTED, sender=long_sender))  # handled
    await h.control(
        _event(T_TURN_CANCELLED, {"rtc_session_id": SESSION}, sender=long_sender)
    )  # dropped (malformed)
    assert h.audit_types() == [EVENT_VOICE_CONTROL_HANDLED, EVENT_VOICE_CONTROL_DROPPED]
    assert all(len(kw["sender_id"]) <= 128 for _, kw in h.audits)


@pytest.mark.asyncio
async def test_drop_audits_are_budgeted_per_room(h):
    h.t._voice_drop_audits = VoiceRateLimiter(limit=3)
    for _ in range(8):
        await h.control(_event(T_CALL_STARTED, sender=AGENT))
    assert h.drop_reasons() == ["non_caller_sender"] * 3
    # Another room has its own budget.
    await h.control(_event(T_CALL_STARTED, sender=AGENT), room=OTHER_ROOM)
    assert len(h.drop_reasons()) == 4


@pytest.mark.asyncio
async def test_malformed_event_type_is_clipped_in_the_audit_row(h):
    await h.t._audit_voice_control_drop(_cred(), ROOM, "@s:h" * 200, "k" * 500, "malformed")
    _, kw = h.audits[-1]
    assert len(kw["details"]["kind"]) == 80 and len(kw["sender_id"]) == 128


@pytest.mark.asyncio
async def test_malformed_control_event_is_ignored(h):
    await h.control(_event(T_TURN_CANCELLED, {"rtc_session_id": SESSION}))
    assert h.acks == [] and not h.known()
    assert h.drop_reasons() == ["malformed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", [("group", 3), ("dm", 3), (None, 3), (None, 0), ("dm", 0)])
async def test_control_event_outside_a_one_to_one_room_is_ignored(h, shape):
    h.shape = shape
    await h.control(_event(T_CALL_STARTED))
    assert h.acks == [] and not h.known()
    h.authorize.assert_not_awaited()


@pytest.mark.asyncio
async def test_count_only_one_to_one_room_is_accepted(h):
    h.shape = (None, 2)
    await h.control(_event(T_CALL_STARTED))
    assert len(h.acks) == 1 and h.known()


@pytest.mark.asyncio
async def test_unauthorized_caller_gets_no_ack(h):
    h.allow = False
    await h.control(_event(T_CALL_STARTED))
    assert h.acks == [] and not h.known()
    assert h.drop_reasons() == ["sender_not_authorized"]


@pytest.mark.asyncio
async def test_no_ack_while_live_voice_is_disabled(h):
    """Kill switch: voice turns run as text and send no binding, so acking
    would make Narra drop every reply of the call."""
    h.t.STREAMING_ENABLED = False
    await h.control(_event(T_CALL_STARTED))
    assert h.acks == [] and not h.known()
    h.authorize.assert_not_awaited()
    assert h.drop_reasons() == ["live_voice_disabled"]


@pytest.mark.asyncio
async def test_session_is_pinned_to_its_first_room_and_caller(h):
    await h.control(_event(T_CALL_STARTED))
    h.acks.clear()
    h.authorize.reset_mock()

    await h.control(_event(T_TURN_CANCELLED), room=OTHER_ROOM)
    await h.control(_event(T_TURN_CANCELLED, sender="@mallory:matrix.netmind.chat"))

    assert h.acks == []
    h.authorize.assert_not_awaited()
    assert h.drop_reasons() == ["session_pinned_elsewhere"] * 2
    # The foreign cancel must not have tombstoned or cancelled the real turn.
    assert not h.t._voice_registry.turn_is_dead("agent_x", ROOM, SESSION, TURN)
    assert await h.t._claim_voice_turn(_cred(), _voice_msg()) is True


@pytest.mark.asyncio
async def test_known_session_is_not_reauthorized(h):
    await h.control(_event(T_CALL_STARTED))
    await h.control(_event(T_TURN_HINT))
    assert h.authorize.await_count == 1
    assert [a["content"]["ack_of"] for a in h.acks] == ["call.started", "turn_hint"]
    assert h.acks[1]["content"]["turn_id"] == TURN


@pytest.mark.asyncio
async def test_session_is_learned_from_any_control_event(h):
    """After a restart the process has no registry; Narra's re-sent
    hint / cancel re-teaches the call (they only reach v15 agents)."""
    await h.control(_event(T_TURN_HINT))
    assert h.known() and [a["content"]["ack_of"] for a in h.acks] == ["turn_hint"]


@pytest.mark.asyncio
async def test_resent_control_event_is_acked_with_the_same_txn(h):
    await h.control(_event(T_CALL_STARTED))
    await h.control(_event(T_CALL_STARTED))
    assert len(h.acks) == 2 and h.acks[0]["txn"] == h.acks[1]["txn"]


@pytest.mark.asyncio
async def test_cancel_is_acked_and_tombstones_the_turn(h):
    await h.control(_event(T_CALL_STARTED))
    await h.control(_event(T_TURN_CANCELLED))

    assert h.acks[-1]["content"] == {
        "ack_of": "turn.cancelled",
        "rtc_session_id": SESSION,
        "turn_id": TURN,
        "platform": "nexus",
        "call_mode": "realtime",
        "supports_long_task_delivery": False,
    }
    h.acks.clear()
    # The voice input for that turn arrives late: it must not run or be acked.
    assert h.t._voice_registry.turn_is_dead("agent_x", ROOM, SESSION, TURN)
    # A normal cancel is not a storage failure.
    assert EVENT_DEDUP_FAIL_OPEN not in h.audit_types()
    assert await h.t._claim_voice_turn(_cred(), _voice_msg()) is False
    await h.settle()
    assert h.acks == []
    assert h.audits[-1][0] == EVENT_INGRESS_DROPPED_DEDUP
    # A different turn of the same call is unaffected.
    assert await h.t._claim_voice_turn(_cred(), _voice_msg(turn="T-next", event_id="$in2")) is True


@pytest.mark.asyncio
async def test_call_ended_tears_the_call_down(h):
    await h.control(_event(T_CALL_STARTED))
    await h.control(_event(T_CALL_ENDED))
    assert h.acks[-1]["content"]["ack_of"] == "call.ended"
    assert "turn_id" not in h.acks[-1]["content"]
    assert not h.known()
    assert h.t._voice_registry.turn_is_dead("agent_x", ROOM, SESSION, "any-turn")


@pytest.mark.asyncio
async def test_call_ended_for_an_untracked_call_does_nothing(h):
    await h.control(_event(T_CALL_ENDED))
    assert h.acks == [] and not h.known()
    h.authorize.assert_not_awaited()


@pytest.mark.asyncio
async def test_ack_failure_is_retried_then_audited_never_raised(h):
    h.send_error = mt_mod.MatrixSendError("http_500", "boom")
    await h.control(_event(T_CALL_STARTED))  # must not raise
    assert h.acks == []
    assert h.audit_types() == [EVENT_VOICE_CONTROL_HANDLED, EVENT_TRANSPORT_SEND_FAILED]
    failed = h.audits[1][1]["details"]
    assert failed["ack_of"] == "call.started"
    assert failed["attempts"] == h.t.VOICE_ACK_MAX_ATTEMPTS
    # The call is tracked regardless: only the ack was lost.
    assert h.known()


@pytest.mark.asyncio
async def test_new_sessions_are_rate_limited_per_room(h):
    """A client spraying control events with made-up session ids must not
    turn each one into an authorize-event call and an ack — and the flood
    is one audit row, not one per event."""
    h.t._voice_admissions = VoiceRateLimiter(limit=3)
    for i in range(10):
        await h.control(
            _event(T_TURN_HINT, {"rtc_session_id": f"S{i}", "turn_id": TURN})
        )
    assert h.authorize.await_count == 3 and len(h.acks) == 3
    assert h.drop_reasons() == ["new_session_rate_limited"]
    assert len(h.t._voice_registry) == 1  # one current call per room
    # The room's current session is not subject to the new-session limit.
    h.acks.clear()
    await h.control(_event(T_TURN_HINT, {"rtc_session_id": "S2", "turn_id": "T9"}))
    assert [a["content"]["turn_id"] for a in h.acks] == ["T9"]


@pytest.mark.asyncio
async def test_known_session_events_are_rate_limited_per_room(h):
    """The pinned caller spraying cancels with random turn ids: each would
    write a dedup row, an ack event and an audit row."""
    h.t._voice_events = VoiceRateLimiter(limit=4)
    await h.control(_event(T_CALL_STARTED))
    for i in range(10):
        await h.control(
            _event(T_TURN_CANCELLED, {"rtc_session_id": SESSION, "turn_id": f"t{i}"})
        )
    assert len(h.acks) == 4  # call.started + three cancels
    assert h.drop_reasons() == ["room_rate_limited"]
    assert len(h.t._voice_registry.get("agent_x", SESSION).cancelled_turns) == 3


@pytest.mark.asyncio
async def test_cancel_past_the_cancelled_set_cap_is_dropped(h):
    await h.control(_event(T_CALL_STARTED))
    h.t._voice_registry.MAX_CANCELLED_TURNS = 1
    await h.control(_event(T_TURN_CANCELLED, {"rtc_session_id": SESSION, "turn_id": "t0"}))
    h.acks.clear()
    await h.control(_event(T_TURN_CANCELLED, {"rtc_session_id": SESSION, "turn_id": "t1"}))
    assert h.acks == [] and h.drop_reasons() == ["cancelled_turn_cap"]


@pytest.mark.asyncio
async def test_events_of_one_new_session_share_a_single_admission(h):
    """A reconnect replays several events of the same call at once: they
    must cost one authorize-event call and one admission slot, not one each."""
    h.t._voice_admissions = VoiceRateLimiter(limit=1)
    gate = asyncio.Event()

    async def _slow_authorize(*a, **k):
        await gate.wait()
        return _AuthorizeVerdict(allow=True)

    h.authorize.side_effect = _slow_authorize
    tasks = [
        asyncio.create_task(
            h.t._handle_voice_control_event(_cred(), ROOM, event)
        )
        for event in (
            _event(T_CALL_STARTED),
            _event(T_TURN_HINT),
            _event(T_TURN_CANCELLED, {"rtc_session_id": SESSION, "turn_id": "t-old"}),
        )
    ]
    for _ in range(3):
        await asyncio.sleep(0)
    gate.set()
    await asyncio.gather(*tasks)
    await h.settle()

    assert h.authorize.await_count == 1
    assert sorted(a["content"]["ack_of"] for a in h.acks) == [
        "call.started", "turn.cancelled", "turn_hint",
    ]
    assert h.drop_reasons() == [] and h.t._voice_admitting == {}


@pytest.mark.asyncio
async def test_shared_admission_refusal_reaches_every_waiter(h):
    h.allow = False
    gate = asyncio.Event()

    async def _slow_authorize(*a, **k):
        await gate.wait()
        return _AuthorizeVerdict(allow=False)

    h.authorize.side_effect = _slow_authorize
    tasks = [
        asyncio.create_task(
            h.t._handle_voice_control_event(_cred(), ROOM, event)
        )
        for event in (_event(T_CALL_STARTED), _event(T_TURN_HINT))
    ]
    for _ in range(3):
        await asyncio.sleep(0)
    gate.set()
    await asyncio.gather(*tasks)
    await h.settle()

    assert h.authorize.await_count == 1 and h.acks == [] and not h.known()
    assert h.drop_reasons() == ["sender_not_authorized"] * 2


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["raise", "cancel"])
async def test_crashed_shared_admission_fails_closed_for_every_waiter(h, failure):
    """The event running the admission dies (an error, or stop() cancelling
    it). The events waiting on it must neither hang nor be admitted: no
    registry entry, no ack — never a pass without authorize-event."""
    started = asyncio.Event()
    gate = asyncio.Event()

    async def _authorize(*a, **k):
        started.set()
        await gate.wait()
        raise RuntimeError("authorize exploded")

    h.authorize.side_effect = _authorize
    leader = asyncio.create_task(
        h.t._handle_voice_control_event(_cred(), ROOM, _event(T_CALL_STARTED))
    )
    await started.wait()
    waiter = asyncio.create_task(
        h.t._handle_voice_control_event(_cred(), ROOM, _event(T_TURN_HINT))
    )
    for _ in range(3):
        await asyncio.sleep(0)
    if failure == "raise":
        gate.set()
        with pytest.raises(RuntimeError):
            await leader
    else:
        leader.cancel()
        with pytest.raises(asyncio.CancelledError):
            await leader
    await asyncio.wait_for(waiter, timeout=1)
    await h.settle()

    assert h.acks == [] and not h.known()
    assert h.drop_reasons() == ["admission_failed"]
    assert h.t._voice_admitting == {}


@pytest.mark.asyncio
async def test_late_event_of_a_previous_call_cannot_displace_the_current_one(h):
    await h.control(_event(T_CALL_STARTED, {"rtc_session_id": "A"}))
    await h.control(_event(T_CALL_ENDED, {"rtc_session_id": "A"}))
    await h.control(_event(T_CALL_STARTED, {"rtc_session_id": "B"}))
    h.acks.clear()
    h.authorize.reset_mock()

    await h.control(_event(T_TURN_HINT, {"rtc_session_id": "A", "turn_id": "t-late"}))

    assert h.acks == [] and h.drop_reasons() == ["session_retired"]
    h.authorize.assert_not_awaited()
    assert h.t._voice_registry.matches("agent_x", "B", ROOM, CALLER)


@pytest.mark.asyncio
async def test_slow_ack_does_not_hold_the_handler(h):
    """The handler settles the registry and the tombstone, then hands the
    ack to its own task: a slow homeserver must not delay cancel handling."""
    h.send_gate = asyncio.Event()
    await h.t._handle_voice_control_event(_cred(), ROOM, _event(T_CALL_STARTED))
    await h.t._handle_voice_control_event(_cred(), ROOM, _event(T_TURN_CANCELLED))
    # Both handlers returned with their acks still in flight.
    assert h.acks == [] and len(h.t._voice_control_tasks) == 2
    assert h.t._voice_registry.turn_is_dead("agent_x", ROOM, SESSION, TURN)
    assert await h.t._claim_voice_turn(_cred(), _voice_msg()) is False
    h.send_gate.set()
    await h.settle()
    assert len(h.acks) == 2


@pytest.mark.asyncio
async def test_same_new_session_racing_from_two_rooms_is_pinned_to_one(h):
    """Both events see the session as unknown and both pass admission; only
    the room that learned it first may tombstone and ack."""
    gate = asyncio.Event()

    async def _slow_authorize(*a, **k):
        await gate.wait()
        return _AuthorizeVerdict(allow=True)

    h.authorize.side_effect = _slow_authorize
    first = asyncio.create_task(h.control(_event(T_TURN_CANCELLED), room=ROOM))
    second = asyncio.create_task(h.control(_event(T_TURN_CANCELLED), room=OTHER_ROOM))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    gate.set()
    await asyncio.gather(first, second)
    await h.settle()

    assert len(h.acks) == 1
    winner = h.acks[0]["room"]
    loser = OTHER_ROOM if winner == ROOM else ROOM
    assert h.drop_reasons() == ["session_pinned_elsewhere"]
    # The loser's room was not tombstoned: its key is still claimable there.
    assert await h.t._claim_voice_turn(_cred(), _voice_msg(room=loser)) is True
    assert await h.t._claim_voice_turn(_cred(), _voice_msg(room=winner)) is False


@pytest.mark.asyncio
async def test_tombstone_db_fail_open_is_audited(h):
    await h.control(_event(T_CALL_STARTED))
    h.t._dedup_store.classify = AsyncMock(
        return_value={"accept": True, "layer": "db_fail_open", "error": "boom"}
    )
    await h.control(_event(T_TURN_CANCELLED))
    fail_open = [kw for event_type, kw in h.audits if event_type == EVENT_DEDUP_FAIL_OPEN]
    assert len(fail_open) == 1
    assert fail_open[0]["details"]["context"] == "voice_turn_tombstone"


# ── turn claim ──────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_same_logical_turn_is_claimed_once_across_event_ids(h):
    assert await h.t._claim_voice_turn(_cred(), _voice_msg(event_id="$a")) is True
    assert await h.t._claim_voice_turn(_cred(), _voice_msg(event_id="$b")) is False
    assert h.audits[-1][0] == EVENT_INGRESS_DROPPED_DEDUP


@pytest.mark.asyncio
async def test_turn_started_is_acked_only_inside_a_known_call(h):
    # No call.started seen (a v14 agent never receives one): claim, no ack.
    assert await h.t._claim_voice_turn(_cred(), _voice_msg(turn="T0", event_id="$0")) is True
    await h.settle()
    assert h.acks == []

    await h.control(_event(T_CALL_STARTED))
    h.acks.clear()
    assert await h.t._claim_voice_turn(_cred(), _voice_msg()) is True
    await h.settle()
    assert [a["content"] for a in h.acks] == [
        {
            "ack_of": "turn.started",
            "rtc_session_id": SESSION,
            "turn_id": TURN,
            "platform": "nexus",
            "call_mode": "realtime",
            "supports_long_task_delivery": False,
        }
    ]


@pytest.mark.asyncio
async def test_turn_from_another_room_or_sender_is_not_acked(h):
    await h.control(_event(T_CALL_STARTED))
    h.acks.clear()
    assert await h.t._claim_voice_turn(_cred(), _voice_msg(turn="T1", room=OTHER_ROOM)) is True
    assert await h.t._claim_voice_turn(_cred(), _voice_msg(turn="T2", sender="@m:h")) is True
    await h.settle()
    assert h.acks == []


@pytest.mark.asyncio
async def test_text_and_degraded_turns_pass_the_claim_untouched(h):
    text = SimpleNamespace(chat_id=ROOM, message_id="$t", sender_id=CALLER, raw={})
    for _ in range(2):
        assert await h.t._claim_voice_turn(_cred(), text) is True
        assert await h.t._claim_voice_turn(_cred(), _voice_msg(degraded=True)) is True
    await h.settle()
    assert h.acks == [] and h.audits == []


@pytest.mark.asyncio
async def test_claim_db_fail_open_runs_the_turn_and_is_audited(h):
    """The durable layer is down: the turn must still run (failing closed
    would mute the call), but the lost at-most-once guarantee leaves a row."""
    h.t._dedup_store.classify = AsyncMock(
        return_value={"accept": True, "layer": "db_fail_open", "error": "db down"}
    )
    audit_gate = asyncio.Event()
    real_audit = h.t._audit

    async def _slow_audit(event_type, **kw):
        await audit_gate.wait()
        await real_audit(event_type, **kw)

    h.t._audit = _slow_audit
    # The claim returns without waiting for the (failing) DB a second time.
    assert await asyncio.wait_for(h.t._claim_voice_turn(_cred(), _voice_msg()), 1) is True
    assert h.audits == []
    audit_gate.set()
    await h.settle()
    assert [
        kw["details"] for event_type, kw in h.audits if event_type == EVENT_DEDUP_FAIL_OPEN
    ] == [{"error": "db down", "context": "voice_turn_claim", "turn_id": TURN}]


@pytest.mark.asyncio
async def test_normal_claim_writes_no_fail_open_audit(h):
    assert await h.t._claim_voice_turn(_cred(), _voice_msg()) is True
    assert h.audits == []


@pytest.mark.asyncio
async def test_claim_without_a_dedup_store_still_runs_the_turn(h):
    h.t._dedup_store = None
    assert await h.t._claim_voice_turn(_cred(), _voice_msg()) is True
    assert await h.t._claim_voice_turn(_cred(), _voice_msg()) is True


@pytest.mark.asyncio
async def test_crashed_voice_task_is_logged_and_untracked(h):
    async def _boom():
        raise RuntimeError("boom")

    h.t._spawn_voice_task(_boom(), label="probe")
    task = next(iter(h.t._voice_control_tasks))
    with pytest.raises(RuntimeError):
        await task
    await asyncio.sleep(0)
    assert h.t._voice_control_tasks == set()


@pytest.mark.asyncio
async def test_stop_cancels_in_flight_voice_tasks(h, monkeypatch):
    gate = asyncio.Event()

    async def _wait():
        await gate.wait()

    h.t._spawn_voice_task(_wait(), label="probe")
    task = next(iter(h.t._voice_control_tasks))
    await h.t.stop()
    await asyncio.sleep(0)
    assert task.cancelled() and h.t._voice_control_tasks == set()

    # The sync loop and workers wind down a moment after stop(): a task
    # they try to start in that gap must not be started at all.
    started = []

    async def _late():
        started.append(1)

    assert h.t._spawn_voice_task(_late(), label="late") is False
    await asyncio.sleep(0)
    assert started == [] and h.t._voice_control_tasks == set()


@pytest.mark.asyncio
async def test_restarted_trigger_handles_voice_tasks_again(h, monkeypatch):
    """stop() then start() on the same instance: the stopping flag must not
    leave control events silently dropped forever."""
    await h.t.stop()
    monkeypatch.setattr(mt_mod.ChannelTriggerBase, "start", AsyncMock())
    await h.t.start(None)
    await h.control(_event(T_CALL_STARTED))
    assert [a["content"]["ack_of"] for a in h.acks] == ["call.started"]


# ── message path wiring ─────────────────────────────────────────────────
def _dc_voice_msg(turn: str, text: str, *, event_id: str | None = None):
    from dataclasses import make_dataclass

    cls = make_dataclass(
        "M", ["chat_id", "message_id", "sender_id", "content", "raw", "timestamp_ms"]
    )
    rtc = {
        "rtc_session_id": SESSION, "turn_id": turn, "invocation_id": "i",
        "agent_profile_id": "p", "voice_instructions": "Be brief.", "degraded": False,
    }
    return cls(ROOM, event_id or f"$in-{turn}", CALLER, text, {"rtc_voice": rtc}, 0)


@pytest.mark.asyncio
async def test_duplicate_turn_is_dropped_before_the_call_queue(h, monkeypatch):
    """The claim sits at the entry of the voice branch: the first delivery
    runs, a re-delivery under a new event id returns without running and
    without being merged into anything."""
    ran: list[str] = []

    async def _stream(cred, message, sender, *, attachments=None):
        ran.append(message.content)
        return "ok"

    monkeypatch.setattr(h.t, "_build_and_run_agent_streaming", _stream)
    assert await h.t._build_and_run_agent(_cred(), _dc_voice_msg("T1", "one"), "U") == "ok"
    assert (
        await h.t._build_and_run_agent(
            _cred(), _dc_voice_msg("T1", "one", event_id="$resent"), "U"
        )
        == ""
    )
    assert ran == ["one"]


@pytest.mark.asyncio
async def test_turn_rejected_by_a_base_gate_stays_claimable(h, monkeypatch):
    """The claim must not sit in front of the base pipeline's gates: a turn
    the ingress breaker turned away has to run when Narra re-sends it."""
    t = h.t
    t._clients[t._subscriber_key(_cred())] = object()
    monkeypatch.setattr(t, "is_echo", AsyncMock(return_value=False))
    monkeypatch.setattr(t, "_is_mentioning_us", lambda *a, **k: False)
    monkeypatch.setattr(t, "_classify", AsyncMock(return_value="dm"))
    monkeypatch.setattr(t, "_reconcile_room_shape", lambda message, target: message)
    monkeypatch.setattr(t, "_late_voice_upgrade", lambda message, **k: message)
    # The base pipeline drops the turn (breaker open, credential unbound, …).
    monkeypatch.setattr(mt_mod.ChannelTriggerBase, "_process_message", AsyncMock())

    await t._process_message(_cred(), _voice_msg())
    await h.settle()

    assert h.acks == [] and h.audits == []
    assert await t._claim_voice_turn(_cred(), _voice_msg(event_id="$resent")) is True


@pytest.mark.asyncio
async def test_kill_switch_voice_turn_is_not_claimed(h, monkeypatch):
    h.t.STREAMING_ENABLED = False
    claim = AsyncMock(return_value=True)
    monkeypatch.setattr(h.t, "_claim_voice_turn", claim)
    monkeypatch.setattr(h.t, "_build_and_run_agent_atomic", AsyncMock(return_value="text"))
    assert await h.t._build_and_run_agent(_cred(), _dc_voice_msg("T1", "one"), "U") == "text"
    claim.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["cancel", "call_ended"])
async def test_queued_turn_cancelled_before_it_starts_never_runs(h, monkeypatch, ending):
    """T1 is running, T2 is claimed and queued behind it. T2 is then
    cancelled (or the call ends): T2 must not run after T1."""
    ran: list[str] = []
    await h.control(_event(T_CALL_STARTED))

    async def _stream(cred, message, sender, *, attachments=None):
        ran.append(message.content)
        if len(ran) == 1:
            assert await h.t._build_and_run_agent(_cred(), _dc_voice_msg("T2", "two"), "U") == ""
            if ending == "cancel":
                await h.control(
                    _event(T_TURN_CANCELLED, {"rtc_session_id": SESSION, "turn_id": "T2"})
                )
            else:
                await h.control(_event(T_CALL_ENDED))
        return "ok"

    monkeypatch.setattr(h.t, "_build_and_run_agent_streaming", _stream)
    await h.t._build_and_run_agent(_cred(), _dc_voice_msg("T1", "one"), "U")
    await h.settle()

    assert ran == ["one"]
    assert h.t._voice_calls == {}  # the drain loop still released the call
    dropped = [
        kw for event_type, kw in h.audits
        if event_type == EVENT_INGRESS_DROPPED_DEDUP
    ]
    assert [kw["details"] for kw in dropped] == [
        {"dedup_layer": "voice_turn_cancelled", "turn_id": "T2"}
    ]


@pytest.mark.asyncio
async def test_redial_does_not_revive_or_merge_the_previous_calls_queued_turn(
    h, monkeypatch
):
    """Call A: T1 running, T2 queued. The caller hangs up and redials (call
    B) before T1 finishes, and speaks. A's T2 must not run, and its words
    must not be merged into B's first turn."""
    ran: list[str] = []

    def _turn(session: str, turn: str, text: str):
        msg = _dc_voice_msg(turn, text, event_id=f"$in-{session}-{turn}")
        msg.raw["rtc_voice"]["rtc_session_id"] = session
        return msg

    await h.control(_event(T_CALL_STARTED, {"rtc_session_id": "A"}))

    async def _stream(cred, message, sender, *, attachments=None):
        ran.append(message.content)
        if len(ran) == 1:
            await h.t._build_and_run_agent(_cred(), _turn("A", "T2", "a-two"), "U")
            await h.control(_event(T_CALL_ENDED, {"rtc_session_id": "A"}))
            await h.control(_event(T_CALL_STARTED, {"rtc_session_id": "B"}))
            await h.t._build_and_run_agent(_cred(), _turn("B", "T1", "b-one"), "U")
        return "ok"

    monkeypatch.setattr(h.t, "_build_and_run_agent_streaming", _stream)
    await h.t._build_and_run_agent(_cred(), _turn("A", "T1", "a-one"), "U")

    assert ran == ["a-one", "b-one"]


@pytest.mark.asyncio
async def test_cancelled_queued_turn_is_not_merged_into_the_next_one(h, monkeypatch):
    ran: list[str] = []
    await h.control(_event(T_CALL_STARTED))

    async def _stream(cred, message, sender, *, attachments=None):
        ran.append(message.content)
        if len(ran) == 1:
            await h.t._build_and_run_agent(_cred(), _dc_voice_msg("T2", "two"), "U")
            await h.t._build_and_run_agent(_cred(), _dc_voice_msg("T3", "three"), "U")
            await h.control(
                _event(T_TURN_CANCELLED, {"rtc_session_id": SESSION, "turn_id": "T2"})
            )
        return "ok"

    monkeypatch.setattr(h.t, "_build_and_run_agent_streaming", _stream)
    await h.t._build_and_run_agent(_cred(), _dc_voice_msg("T1", "one"), "U")

    assert ran == ["one", "three"]


@pytest.mark.asyncio
async def test_sync_loop_drops_control_events_past_the_per_agent_handler_cap(monkeypatch):
    h = _Harness(monkeypatch)
    monkeypatch.setattr(h.t, "VOICE_CONTROL_MAX_HANDLERS_PER_AGENT", 0)
    yielded = await _drive_one_sync(
        monkeypatch, h, [_event(T_CALL_STARTED), _event(T_TURN_HINT), _event(T_CALL_ENDED)]
    )
    await h.settle()
    assert yielded == [] and h.acks == [] and not h.known()
    # Overflow is a flood by definition: one audit row, not one per event.
    assert h.drop_reasons() == ["handler_cap"]


@pytest.mark.asyncio
async def test_kill_switch_short_circuits_at_dispatch(monkeypatch):
    """The switch is flipped when the system is under strain: no handler
    slot, no task per event, and one audit row per agent per minute."""
    h = _Harness(monkeypatch)
    h.t.STREAMING_ENABLED = False
    handler = AsyncMock()
    monkeypatch.setattr(h.t, "_handle_voice_control_event", handler)

    for _ in range(20):
        h.t._dispatch_voice_control_event(_cred(), ROOM, _event(T_CALL_STARTED))
    assert len(h.t._voice_control_tasks) == 1  # the single audit task
    assert h.t._voice_handlers_in_flight == {}
    await h.settle()

    handler.assert_not_awaited()
    assert h.drop_reasons() == ["live_voice_disabled"] and h.acks == []


@pytest.mark.asyncio
async def test_handler_slots_are_per_agent_and_released(monkeypatch):
    """One agent's in-flight handlers must not use up another agent's, and
    a finished handler gives its slot back."""
    h = _Harness(monkeypatch)
    monkeypatch.setattr(h.t, "VOICE_CONTROL_MAX_HANDLERS_PER_AGENT", 1)
    gate = asyncio.Event()

    async def _slow_authorize(*a, **k):
        await gate.wait()
        return _AuthorizeVerdict(allow=True)

    h.authorize.side_effect = _slow_authorize
    other = _cred()
    other.agent_id = "agent_y"

    h.t._dispatch_voice_control_event(_cred(), ROOM, _event(T_CALL_STARTED))
    h.t._dispatch_voice_control_event(_cred(), ROOM, _event(T_TURN_HINT))
    h.t._dispatch_voice_control_event(other, OTHER_ROOM, _event(T_CALL_STARTED))
    await asyncio.sleep(0)
    assert h.t._voice_handlers_in_flight == {"agent_x": 1, "agent_y": 1}

    gate.set()
    await h.settle()
    assert h.t._voice_handlers_in_flight == {}
    assert h.drop_reasons() == ["handler_cap"]  # agent_x's second event only
    assert sorted(a["room"] for a in h.acks) == sorted([ROOM, OTHER_ROOM])

    # The slot is free again.
    h.acks.clear()
    h.t._dispatch_voice_control_event(_cred(), ROOM, _event(T_TURN_HINT))
    await h.settle()
    assert [a["content"]["ack_of"] for a in h.acks] == ["turn_hint"]


@pytest.mark.asyncio
async def test_handler_slot_is_released_when_cancelled_before_it_runs(monkeypatch):
    """A task cancelled before its first step never runs a ``finally`` of
    its own; the slot must come back anyway, or repeated stop/start would
    lock the agent out of control events for good."""
    h = _Harness(monkeypatch)
    h.t._dispatch_voice_control_event(_cred(), ROOM, _event(T_CALL_STARTED))
    assert h.t._voice_handlers_in_flight == {"agent_x": 1}
    task = next(iter(h.t._voice_control_tasks))
    task.cancel()  # before the event loop ever ran it
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)
    assert h.t._voice_handlers_in_flight == {}


@pytest.mark.asyncio
async def test_dispatch_while_stopping_takes_no_slot(monkeypatch):
    h = _Harness(monkeypatch)
    h.t._voice_handlers_in_flight["agent_x"] = 2  # two handlers genuinely running
    h.t._voice_stopping = True
    h.t._dispatch_voice_control_event(_cred(), ROOM, _event(T_CALL_STARTED))
    assert h.t._voice_handlers_in_flight == {"agent_x": 2}
    assert h.t._voice_control_tasks == set()


@pytest.mark.asyncio
async def test_start_clears_voice_accounting_left_by_stop(h, monkeypatch):
    h.t._voice_handlers_in_flight["agent_x"] = 5
    h.t._voice_admitting[("agent_x", SESSION, ROOM, CALLER)] = object()
    monkeypatch.setattr(mt_mod.ChannelTriggerBase, "start", AsyncMock())
    await h.t.start(None)
    assert h.t._voice_handlers_in_flight == {} and h.t._voice_admitting == {}


# ── /sync loop wiring ───────────────────────────────────────────────────
class _SyncResp(SimpleNamespace):
    """Stands in for nio.SyncResponse (connect() isinstance-checks it)."""


async def _drive_one_sync(monkeypatch, h, timeline_events: list) -> list:
    """Run the real connect() over one /sync batch carrying ``timeline_events``."""
    t = h.t

    class _SyncClient:
        def __init__(self, **_kw):
            self.device_id = None
            self.access_token = None
            self.user_id = None
            self._synced = False

        async def sync(self, timeout=None, since=None, full_state=False):
            if self._synced:
                t.running = False
                return _SyncResp(rooms=SimpleNamespace(invite={}, join={}), next_batch="end")
            self._synced = True
            room = SimpleNamespace(
                state=SimpleNamespace(events=[]),
                timeline=SimpleNamespace(events=timeline_events),
            )
            return _SyncResp(
                rooms=SimpleNamespace(invite={}, join={ROOM: room}), next_batch="b1"
            )

        async def close(self):
            return None

    monkeypatch.setattr(mt_mod, "AsyncClient", _SyncClient)
    monkeypatch.setattr(mt_mod, "SyncResponse", _SyncResp)
    t.running = True
    return [raw async for raw in t.connect(_cred())]


@pytest.mark.asyncio
async def test_sync_loop_handles_control_events_and_never_yields_them(monkeypatch):
    h = _Harness(monkeypatch)
    yielded = await _drive_one_sync(
        monkeypatch,
        h,
        [
            _event(T_CALL_STARTED),
            # Our own ack echoed back by /sync: not a control event.
            _event(VOICE_ACK_EVENT_TYPE, {"ack_of": "call.started"}, sender=AGENT),
        ],
    )
    await h.settle()

    assert yielded == []
    assert [a["content"]["ack_of"] for a in h.acks] == ["call.started"]
    assert h.known()
