"""
@file_name: _voice_call.py
@date: 2026-11-19
@description: Voice contract v15 — control events, acknowledgements, call registry.

Narra delivers four downlink control events to agents installed at guide
version 15, as Matrix custom timeline events sent with the CALLER's
identity into the call's 1:1 room:

    ai.netmind.rtc.agent.call.started.v2      {rtc_session_id, sent_at, deadline_ms, voice_rules}
    ai.netmind.rtc.agent.turn_hint.v2         {rtc_session_id, turn_id, long_task_hint, sent_at}
    ai.netmind.rtc.agent.turn.cancelled.v2    {rtc_session_id, turn_id, sent_at}
    ai.netmind.rtc.agent.call.ended.v2        {rtc_session_id, sent_at}

(``turn.started.v2`` is not a standalone event: it rides inside the voice
input ``m.room.message`` and is handled on the message path.)

The agent answers each with ``ai.netmind.rtc.agent.ack.v2`` from its own
Matrix identity in the same room. Narra silently ignores an ack that fails
validation, so the shapes built here are exact: call-level acks must not
carry a ``turn_id`` key at all.

Everything in this file is pure (no I/O); the trigger owns transport,
authorization and the durable turn claim. Control events are NOT user
prompts and never start an agent run.

Contract source: Narra's "NarraNexus upgrade to Narra voice-call contract
v15" handoff (2026-11-19), sections 3.0, C3-C7, C12.
"""
from __future__ import annotations

import hashlib
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

VOICE_ACK_EVENT_TYPE = "ai.netmind.rtc.agent.ack.v2"

# Audit rows for the control plane (plain strings; the audit column is a
# VARCHAR). Channel-specific, so they live here rather than in the generic
# channel audit vocabulary.
EVENT_VOICE_CONTROL_HANDLED = "voice_control_handled"
EVENT_VOICE_CONTROL_DROPPED = "voice_control_dropped"

KIND_CALL_STARTED = "call.started"
KIND_TURN_STARTED = "turn.started"
KIND_TURN_HINT = "turn_hint"
KIND_TURN_CANCELLED = "turn.cancelled"
KIND_CALL_ENDED = "call.ended"

# Matrix event type -> the ``ack_of`` value that answers it.
VOICE_CONTROL_EVENT_KINDS: dict[str, str] = {
    "ai.netmind.rtc.agent.call.started.v2": KIND_CALL_STARTED,
    "ai.netmind.rtc.agent.turn_hint.v2": KIND_TURN_HINT,
    "ai.netmind.rtc.agent.turn.cancelled.v2": KIND_TURN_CANCELLED,
    "ai.netmind.rtc.agent.call.ended.v2": KIND_CALL_ENDED,
}

_TURN_KINDS = frozenset({KIND_TURN_STARTED, KIND_TURN_HINT, KIND_TURN_CANCELLED})

# What this runtime reports about itself in every ack. ``realtime``: the
# trigger holds a continuous /sync long-poll. Background (dm_report)
# delivery of long tasks is not implemented, so it must be reported False.
VOICE_ACK_PLATFORM = "nexus"
VOICE_ACK_CALL_MODE = "realtime"
VOICE_ACK_SUPPORTS_LONG_TASK_DELIVERY = False


@dataclass(frozen=True)
class VoiceControlEvent:
    """One validated downlink control event."""

    kind: str
    rtc_session_id: str
    turn_id: Optional[str] = None


def _non_blank_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def parse_voice_control_event(
    event_type: Any, content: Any
) -> Optional[VoiceControlEvent]:
    """Validate a custom timeline event as a v15 control event.

    Returns None for any other event type and for a malformed payload
    (missing / blank ``rtc_session_id``; a turn-level event without a
    ``turn_id``). Ids are opaque strings — today they are UUIDs, but the
    contract tells agents not to rely on that. A ``turn_id`` on a
    call-level event is ignored rather than rejected.
    """
    kind = VOICE_CONTROL_EVENT_KINDS.get(event_type) if isinstance(event_type, str) else None
    if kind is None or not isinstance(content, dict):
        return None
    session_id = content.get("rtc_session_id")
    if not _non_blank_str(session_id):
        return None
    turn_id: Optional[str] = None
    if kind in _TURN_KINDS:
        turn_id = content.get("turn_id")
        if not _non_blank_str(turn_id):
            return None
    return VoiceControlEvent(kind=kind, rtc_session_id=session_id, turn_id=turn_id)


def build_voice_ack(
    kind: str, rtc_session_id: str, turn_id: Optional[str] = None
) -> dict:
    """The ``ack.v2`` content answering one control event / voice turn.

    Turn-level acks carry ``turn_id``; call-level acks must not have the
    key at all (Narra rejects the whole ack even for a null value).
    """
    content: dict[str, Any] = {
        "ack_of": kind,
        "rtc_session_id": rtc_session_id,
        "platform": VOICE_ACK_PLATFORM,
        "call_mode": VOICE_ACK_CALL_MODE,
        "supports_long_task_delivery": VOICE_ACK_SUPPORTS_LONG_TASK_DELIVERY,
    }
    if kind in _TURN_KINDS:
        if not _non_blank_str(turn_id):
            raise ValueError(f"ack_of={kind} requires a turn_id")
        content["turn_id"] = turn_id
    return content


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


def voice_ack_txn_id(
    kind: str, rtc_session_id: str, turn_id: Optional[str] = None
) -> str:
    """Deterministic Matrix transaction id for one logical ack.

    The same logical ack can be sent more than once (a control event that
    does arrive twice, our own retry after a failed send); a stable txn id
    makes the homeserver collapse those into one room event. Hashed because
    the ids are opaque strings of unknown length and character set.
    """
    return "nx-ack-" + _digest(kind, rtc_session_id, turn_id or "")[:40]


def voice_turn_claim_key(room_id: str, rtc_session_id: str, turn_id: str) -> str:
    """Durable dedup key for one logical voice turn.

    Claimed (atomically, in the channel dedup store) before any model or
    tool runs, so a turn re-delivered under a different event id runs at
    most once. A ``turn.cancelled`` claims the same key, which is what
    makes it a tombstone: a voice input for that turn arriving later can
    no longer be claimed.

    The room is part of the key: a call lives in exactly one room, and
    without it a member of some OTHER room could burn a turn's key by
    quoting its ids there.
    """
    return "voice-turn:" + _digest(room_id, rtc_session_id, turn_id)[:48]


@dataclass
class VoiceCall:
    """A call this agent has been told about by a control event."""

    rtc_session_id: str
    room_id: str
    caller_mxid: str
    learned_at: float
    # Turns Narra cancelled. Queried by the per-call drain loop so a turn
    # that was claimed and is still waiting behind a running one does not
    # start after its cancellation.
    cancelled_turns: set = field(default_factory=set)
    # Set by ``call.ended``. The entry stays the room's current call (until
    # expiry or until the room's next call retires it) so turns still queued
    # are not started.
    ended: bool = False


@dataclass
class _RoomCalls:
    current: Optional[VoiceCall] = None
    # Session ids of this room's earlier calls, newest last.
    retired: deque = field(default_factory=deque)


class VoiceCallRegistry:
    """In-memory record of v15 calls, one current call per ``(agent, room)``.

    A session is learned from ANY control event, not only ``call.started``:
    control events reach only agents installed at v15, so seeing one is
    itself the proof that the call is on the v15 contract.

    Each session is pinned to the room and caller it was first seen with;
    later events for it must come from both.

    A 1:1 room holds one call at a time. Learning a new session RETIRES the
    room's previous one rather than forgetting it: the room's turn queue is
    shared across calls, so a turn of the earlier call may still be waiting
    there when the caller hangs up and redials, and it must stay dead. A
    retired session can never become current again — a late event of an old
    call must not push out the live one. The retired list is capped, so the
    registry is bounded by rooms, not by how many session ids anyone can
    make up.

    The registry is not the source of truth for dedup (that is the durable
    turn claim). Losing it on a restart costs acks, not correctness.

    A current call is FORGOTTEN after ``ttl_seconds`` (Narra keeps a call
    record for at most 7200 s), so a call whose ``call.ended`` never arrived
    cannot pin memory forever. Forgotten, not retired: retiring means "this
    call is over, its turns are dead", and age alone does not prove that — a
    call that outlives the TTL must keep being answered (it only loses its
    acks until its next control event teaches it back).
    """

    DEFAULT_TTL_SECONDS = 7200.0
    MAX_RETIRED_PER_ROOM = 8
    # More cancelled turns than any real call produces; past it further
    # cancels are refused (see ``mark_cancelled``) so one call cannot grow
    # the set without bound.
    MAX_CANCELLED_TURNS = 1024

    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._rooms: dict[tuple[str, str], _RoomCalls] = {}
        self._by_session: dict[tuple[str, str], VoiceCall] = {}

    def _retire(self, agent_id: str, call: VoiceCall) -> None:
        self._by_session.pop((agent_id, call.rtc_session_id), None)
        room = self._rooms.get((agent_id, call.room_id))
        if room is None:
            return
        if room.current is call:
            room.current = None
        room.retired.append(call.rtc_session_id)
        while len(room.retired) > self.MAX_RETIRED_PER_ROOM:
            room.retired.popleft()

    def _live(self, agent_id: str, rtc_session_id: str) -> Optional[VoiceCall]:
        call = self._by_session.get((agent_id, rtc_session_id))
        if call is not None and call.learned_at < self._clock() - self._ttl:
            # Expired: forget it (NOT retire — see the class docstring).
            del self._by_session[(agent_id, rtc_session_id)]
            room = self._rooms.get((agent_id, call.room_id))
            if room is not None and room.current is call:
                room.current = None
            return None
        return call

    def get(self, agent_id: str, rtc_session_id: str) -> Optional[VoiceCall]:
        """The room's current call for this session, or None."""
        return self._live(agent_id, rtc_session_id)

    def is_retired(self, agent_id: str, room_id: str, rtc_session_id: str) -> bool:
        """True when the session is one of this room's EARLIER calls, i.e.
        it was replaced by a later call in the same room."""
        room = self._rooms.get((agent_id, room_id))
        return room is not None and rtc_session_id in room.retired

    def learn(
        self, agent_id: str, rtc_session_id: str, room_id: str, caller_mxid: str
    ) -> Optional[VoiceCall]:
        """Record a session as its room's current call and return the entry.

        The first room / caller seen for a session wins: when the session
        is already current somewhere the EXISTING entry is returned
        unchanged, and the caller must check it against the room and sender
        in hand. Returns None for a session this room already retired.
        """
        call = self._live(agent_id, rtc_session_id)
        if call is not None:
            return call
        if self.is_retired(agent_id, room_id, rtc_session_id):
            return None
        room = self._rooms.setdefault((agent_id, room_id), _RoomCalls())
        if room.current is not None:
            self._retire(agent_id, room.current)
        call = VoiceCall(
            rtc_session_id=rtc_session_id,
            room_id=room_id,
            caller_mxid=caller_mxid,
            learned_at=self._clock(),
        )
        room.current = call
        self._by_session[(agent_id, rtc_session_id)] = call
        return call

    def matches(
        self, agent_id: str, rtc_session_id: str, room_id: str, sender_mxid: str
    ) -> bool:
        """True iff the session is a live call pinned to this room and sender."""
        call = self._live(agent_id, rtc_session_id)
        return (
            call is not None
            and not call.ended
            and call.room_id == room_id
            and call.caller_mxid == sender_mxid
        )

    def mark_cancelled(self, agent_id: str, rtc_session_id: str, turn_id: str) -> bool:
        """Record a cancelled turn. False when the call is unknown or its
        cancelled set is full (the caller then drops the event)."""
        call = self._live(agent_id, rtc_session_id)
        if call is None:
            return False
        if turn_id in call.cancelled_turns:
            return True
        if len(call.cancelled_turns) >= self.MAX_CANCELLED_TURNS:
            return False
        call.cancelled_turns.add(turn_id)
        return True

    def end(self, agent_id: str, rtc_session_id: str) -> None:
        call = self._live(agent_id, rtc_session_id)
        if call is not None:
            call.ended = True

    def turn_is_dead(
        self, agent_id: str, room_id: str, rtc_session_id: str, turn_id: str
    ) -> bool:
        """True when a queued turn must not start.

        Dead: its call is one this room retired; or its call is the room's
        current one and has ended, or the turn was cancelled. A turn whose
        session this room has no record of is NOT dead — that is every turn
        of a v14 agent, every turn after a restart, and the first turn of a
        new call whose ``call.started`` is still being admitted.
        """
        if self.is_retired(agent_id, room_id, rtc_session_id):
            return True
        call = self._live(agent_id, rtc_session_id)
        return (
            call is not None
            and call.room_id == room_id
            and (call.ended or turn_id in call.cancelled_turns)
        )

    def __len__(self) -> int:
        return len(self._by_session)


class VoiceRateLimiter:
    """Sliding-window cap per ``(agent_id, room_id)``.

    Control events run outside the base pipeline's ingress breaker and its
    bounded worker pool, so they bring their own limits. Two instances are
    used: one on every control event of a room, one (tighter) on admitting
    sessions the process has not seen — each admission costs a room-shape
    lookup and a Narra ``authorize-event`` call. Over a cap the event is
    dropped without an ack, the same safe direction as every other gate.

    ``check`` also says whether a denial is the FIRST of its window, so the
    caller can audit a flood once per window instead of once per event.
    """

    OK = "ok"
    DENIED_FIRST = "denied_first"
    DENIED = "denied"

    def __init__(
        self,
        *,
        limit: int,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limit = limit
        self._window = window_seconds
        self._clock = clock
        self._seen: dict[tuple[str, str], deque] = {}
        self._denied_at: dict[tuple[str, str], float] = {}

    def check(self, agent_id: str, room_id: str) -> str:
        now = self._clock()
        key = (agent_id, room_id)
        stamps = self._seen.setdefault(key, deque())
        while stamps and stamps[0] <= now - self._window:
            stamps.popleft()
        if len(stamps) < self._limit:
            stamps.append(now)
            return self.OK
        last = self._denied_at.get(key)
        if last is None or last <= now - self._window:
            self._denied_at[key] = now
            return self.DENIED_FIRST
        return self.DENIED

    def allow(self, agent_id: str, room_id: str) -> bool:
        return self.check(agent_id, room_id) == self.OK
