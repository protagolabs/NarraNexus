"""
@file_name: _voice_control.py
@date: 2026-11-19
@description: Voice contract v15 — control-plane wiring for the Matrix trigger.

``VoiceControlMixin`` is the part of :class:`MatrixTrigger` that receives
Narra's control events, claims voice turns, and sends ``ack.v2``. The pure
contract logic (event parsing, ack shape, call registry, rate limiter) is in
``_voice_call``; this file is the I/O around it.

It is a mixin, not a collaborator object, because every method here reads the
trigger's own state and gates: the dedup store, the audit writer,
``_authorize_event``, ``_resolve_room_shape``, the streaming kill switch. The
trigger touches it in four places only: ``_init_voice_control`` /
``_reset_voice_control`` / ``_stop_voice_control`` in its lifecycle;
``_is_voice_control_event`` + ``_dispatch_voice_control_event`` in the /sync
loop; one ``_claim_voice_turn`` call at the entry of the voice branch; and
``_drop_dead_voice_turns`` in the per-call drain loop.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

from loguru import logger
from nio import UnknownEvent

from narranexus.platform.channel.channel_audit_events import (
    EVENT_DEDUP_FAIL_OPEN,
    EVENT_INGRESS_DROPPED_DEDUP,
    EVENT_TRANSPORT_SEND_FAILED,
)
from narranexus.platform.schema.parsed_message import ParsedMessage

from .._matrix_send import MatrixSendError, matrix_send_event
from .._narramessenger_credential_manager import NarramessengerCredential
from ._rtc_voice import rtc_voice_stamp, voice_reply_binding
from ._voice_call import (
    EVENT_VOICE_CONTROL_DROPPED,
    EVENT_VOICE_CONTROL_HANDLED,
    KIND_CALL_ENDED,
    KIND_TURN_CANCELLED,
    KIND_TURN_STARTED,
    VOICE_ACK_EVENT_TYPE,
    VOICE_CONTROL_EVENT_KINDS,
    VoiceCallRegistry,
    VoiceControlEvent,
    VoiceRateLimiter,
    build_voice_ack,
    parse_voice_control_event,
    voice_ack_txn_id,
    voice_turn_claim_key,
)

if TYPE_CHECKING:
    # For the type checker the mixin IS a ChannelTriggerBase, so the base's
    # members (``_audit``, ``_dedup_store``) resolve to their real
    # declarations instead of stubs. At runtime it stays a plain mixin and
    # the MRO is MatrixTrigger -> VoiceControlMixin -> ChannelTriggerBase.
    from narranexus.platform.channel.channel_trigger_base import (
        ChannelTriggerBase as _Base,
    )
else:
    _Base = object

# Sentinel refusal: a new-session rate-limit denial that is not the first of
# its window, so the handler drops the event without another audit row.
_ADMISSION_RATE_LIMITED_QUIET = "\x00rate_limited_quiet"


@dataclass(frozen=True)
class _VoiceControlOrigin:
    """The room + sender of a voice control event, in the two-field shape
    ``_authorize_event`` reads off a message (a control event is not a
    message and has no ``ParsedMessage``)."""

    chat_id: str
    sender_id: str


class VoiceControlMixin(_Base):
    """Control events, per-turn claim and acknowledgements (voice contract v15).

    Mixed into :class:`MatrixTrigger`; see the module docstring for the seams.
    """

    if TYPE_CHECKING:
        # Members MatrixTrigger itself defines (its definitions win over
        # these declarations). Base-class members need none: see _Base.
        STREAMING_ENABLED: bool

        async def _authorize_event(
            self, credential: Any, message: Any, *, mentioned: bool
        ) -> Any: ...

        async def _resolve_room_shape(
            self, room_id: str, credential: Any
        ) -> tuple[Any, int]: ...

        def _service_sender_mxid(self, credential: Any) -> str: ...

    def _init_voice_control(self) -> None:
        """Create the control-plane state. Called from the trigger's ``__init__``."""
        # Voice contract v15. The registry pins each live call to its room
        # and caller; the task set holds the in-flight control-event
        # handlers and acks (spawned off the sync loop so one slow HTTP
        # call cannot stall /sync, tracked so none is fire-and-forget).
        self._voice_registry = VoiceCallRegistry()
        # Per (agent, room): every control event, and — tighter — admission
        # of sessions this process has not seen.
        self._voice_events = VoiceRateLimiter(
            limit=self.VOICE_CONTROL_EVENTS_PER_MINUTE
        )
        self._voice_admissions = VoiceRateLimiter(
            limit=self.VOICE_NEW_SESSIONS_PER_MINUTE
        )
        # (agent, session, room, sender) -> the admission in flight for it,
        # so the events of one new session share ONE authorize-event call.
        self._voice_admitting: dict[tuple[str, str, str, str], asyncio.Future] = {}
        # In-flight control handlers per agent (capped), and every voice
        # task (handlers + acks) for stop() and the done-callback.
        self._voice_handlers_in_flight: dict[str, int] = {}
        self._voice_control_tasks: set[asyncio.Task] = set()
        # One audit row per agent per minute for handler-cap overflow.
        self._voice_overflow_audits = VoiceRateLimiter(limit=1)
        # Drop audits per (agent, room) per minute. Every gate that drops a
        # control event writes through this, so no gate — whatever its
        # position — can be used to flood the audit table.
        self._voice_drop_audits = VoiceRateLimiter(
            limit=self.VOICE_CONTROL_DROP_AUDITS_PER_MINUTE
        )
        # Set by stop(): the sync loop and workers are still winding down
        # for a moment after it, and must not start new voice tasks.
        self._voice_stopping = False

    def _reset_voice_control(self) -> None:
        """Called from the trigger's ``start()``."""
        self._voice_stopping = False
        # stop() cancels tasks that may not have run their own cleanup.
        self._voice_handlers_in_flight.clear()
        self._voice_admitting.clear()

    def _stop_voice_control(self) -> None:
        """Called from the trigger's ``stop()``.

        In-flight voice control handlers / acks: nothing to salvage once
        the clients are going away. An event cut off here is not replayed
        (the /sync cursor does not wait for these tasks); the cost is a
        missed ack, which leaves that call on Narra's v14 matching.
        """
        self._voice_stopping = True
        for task in list(self._voice_control_tasks):
            task.cancel()
        self._voice_control_tasks.clear()

    # A control event older than this is a replay of a call long over
    # (cold-start backlog, reconnect catch-up): acting on it would ack a
    # dead session and seed the registry with it. Same window the dedup
    # store uses for historic message replays.
    VOICE_CONTROL_MAX_AGE_MS = 5 * 60 * 1000

    VOICE_ACK_MAX_ATTEMPTS = 2
    VOICE_ACK_RETRY_DELAY_SECONDS = 0.5
    # An ack is only useful while fresh; the default 60 s send budget would
    # keep its task alive for two minutes across the retry.
    VOICE_ACK_TIMEOUT_SECONDS = 10.0

    # Control events run as tasks off the sync loop, outside the base
    # pipeline's ingress breaker and bounded worker pool, so they carry
    # their own limits. A real call produces a few events per turn.
    #   - handlers in flight, per agent (one tenant cannot starve another);
    #   - control events per room per minute;
    #   - admissions of unknown sessions per room per minute (each costs a
    #     Narra authorize-event call).
    VOICE_CONTROL_MAX_HANDLERS_PER_AGENT = 64
    VOICE_CONTROL_EVENTS_PER_MINUTE = 120
    VOICE_NEW_SESSIONS_PER_MINUTE = 6
    # Audit rows for dropped control events, per room per minute. Drops
    # past this are logged at debug only.
    VOICE_CONTROL_DROP_AUDITS_PER_MINUTE = 10
    # Width of channel_trigger_audit.sender_id on MySQL (VARCHAR(128)). A
    # longer value fails the insert there and the whole row is lost, so the
    # sender written to an audit row is clipped to the column, not to the
    # Matrix user-id limit (255).
    VOICE_AUDIT_SENDER_MAX = 128
    # Drop reasons that already come one-per-window from their own limiter.
    # They bypass the per-room drop budget: that budget may be spent by the
    # very flood these rows exist to report.
    _VOICE_SELF_WINDOWED_DROPS = frozenset(
        {"room_rate_limited", "new_session_rate_limited", "handler_cap", "live_voice_disabled"}
    )

    @staticmethod
    def _is_voice_control_event(event: Any) -> bool:
        """True for one of the four v15 downlink control events off /sync.

        nio has no class for these custom types and parses them as
        ``UnknownEvent``; our own acks share the namespace but not these
        exact types, so they fall through to ``_wrap_event`` and are dropped.
        """
        return (
            isinstance(event, UnknownEvent)
            and event.type in VOICE_CONTROL_EVENT_KINDS
        )

    def _spawn_voice_task(
        self, coro: Any, *, label: str, on_done: Optional[Any] = None
    ) -> bool:
        """Run a control-event handler or an ack off the caller's path.

        Tracked and given a done-callback: a bare ``create_task`` would
        swallow any exception until GC (engineering lesson #2). Returns
        False (and closes the coroutine) once the trigger is stopping.

        ``on_done`` runs when the task finishes for ANY reason, including a
        cancel before its first step — a ``finally`` inside the coroutine
        does not run in that case, so per-task accounting belongs here.
        """
        if self._voice_stopping:
            coro.close()
            return False
        task = asyncio.create_task(coro)
        self._voice_control_tasks.add(task)

        def _done(finished: "asyncio.Task") -> None:
            self._voice_control_tasks.discard(finished)
            if on_done is not None:
                on_done()
            if finished.cancelled():
                return
            exc = finished.exception()
            if exc is not None:
                logger.opt(exception=exc).error(
                    f"[matrix:voice] task {label} crashed: "
                    f"{type(exc).__name__}: {exc}"
                )

        task.add_done_callback(_done)
        return True

    def _dispatch_voice_control_event(
        self,
        credential: NarramessengerCredential,
        room_id: str,
        event: Any,
    ) -> None:
        """Hand one control event to a handler task (called by the sync loop).

        The handler does only bounded work — gates, registry, tombstone —
        and spawns the ack separately, so slow homeserver sends never hold a
        handler slot. Past the per-agent cap the event is dropped; that is
        audited from a task of its own (the sync loop does no I/O here).
        """
        agent_id = credential.agent_id
        if not self.STREAMING_ENABLED:
            # The kill switch turns voice turns into plain text turns whose
            # replies carry no binding; acking would make Narra require the
            # binding and drop every one of them. Short-circuit here: the
            # switch is flipped when the system is under strain, so no
            # handler slot, no task per event, and one audit row per agent
            # per minute.
            if self._voice_overflow_audits.allow(agent_id, "live_voice_disabled"):
                self._spawn_voice_task(
                    self._audit_voice_control_drop(
                        credential,
                        room_id,
                        getattr(event, "sender", "") or "",
                        str(event.type),
                        "live_voice_disabled",
                    ),
                    label="audit:live_voice_disabled",
                )
            return
        in_flight = self._voice_handlers_in_flight.get(agent_id, 0)
        if in_flight >= self.VOICE_CONTROL_MAX_HANDLERS_PER_AGENT:
            # Dropped events are a flood by definition: one row per agent
            # per minute, not one per event.
            if self._voice_overflow_audits.allow(agent_id, "handler_cap"):
                self._spawn_voice_task(
                    self._audit_voice_control_drop(
                        credential,
                        room_id,
                        getattr(event, "sender", "") or "",
                        str(event.type),
                        "handler_cap",
                    ),
                    label="audit:handler_cap",
                )
            return

        def _release() -> None:
            left = self._voice_handlers_in_flight.get(agent_id, 0) - 1
            if left > 0:
                self._voice_handlers_in_flight[agent_id] = left
            else:
                self._voice_handlers_in_flight.pop(agent_id, None)

        if self._spawn_voice_task(
            self._handle_voice_control_event(credential, room_id, event),
            label=f"control:{event.type}",
            on_done=_release,
        ):
            self._voice_handlers_in_flight[agent_id] = in_flight + 1

    async def _audit_voice_control_drop(
        self,
        credential: NarramessengerCredential,
        room_id: str,
        sender: str,
        kind: str,
        reason: str,
        **extra: Any,
    ) -> None:
        """Log one dropped control event and audit it, within the room's
        drop-audit budget. ``kind`` and ``sender`` come from the event and
        are clipped: a malformed event's type is whatever the sender wrote."""
        kind = kind[:80]
        if reason not in self._VOICE_SELF_WINDOWED_DROPS and not (
            self._voice_drop_audits.allow(credential.agent_id, room_id)
        ):
            logger.debug(
                f"[matrix:{credential.agent_id}] voice control DROP: {kind} — "
                f"{reason} (room={room_id}; audit budget spent)"
            )
            return
        logger.info(
            f"[matrix:{credential.agent_id}] voice control DROP: {kind} — "
            f"{reason} (room={room_id})"
        )
        await self._audit(
            EVENT_VOICE_CONTROL_DROPPED,
            agent_id=credential.agent_id,
            app_id=getattr(credential, "app_id", ""),
            chat_id=room_id,
            sender_id=sender[: self.VOICE_AUDIT_SENDER_MAX],
            details={"kind": kind, "reason": reason, **extra},
        )

    async def _handle_voice_control_event(
        self,
        credential: NarramessengerCredential,
        room_id: str,
        event: Any,
    ) -> None:
        """Act on one v15 control event and acknowledge it.

        A control event is never a prompt. It is honoured only when ALL of:
        the payload is well-formed; the sender is neither us nor the
        appservice bot; the event is recent; live voice is enabled; the room
        is under its rate limit; and the session is the room's current call
        pinned to this sender, or is new and passes the same gates a voice
        turn does (authoritative 1:1 room and Narra's ``authorize-event``).

        Anything short of that is dropped WITHOUT an ack. That is the safe
        direction: a call whose ``call.started`` was never acked stays on
        Narra's v14 reply matching, exactly as before this contract.

        Every drop writes a ``voice_control_dropped`` audit row with its
        reason, except: a rate-limit denial after the first of its window
        (a flood is one row, not one per event) and a ``call.ended`` for a
        call this process never tracked (nothing happened).
        """
        agent_id = credential.agent_id
        sender = getattr(event, "sender", "") or ""
        source = getattr(event, "source", None)
        content = source.get("content") if isinstance(source, dict) else None
        ctl = parse_voice_control_event(event.type, content)
        kind = ctl.kind if ctl is not None else str(event.type)

        async def _drop(reason: str, **extra: Any) -> None:
            await self._audit_voice_control_drop(
                credential, room_id, sender, kind, reason, **extra
            )

        async def _drop_limited(verdict: str, reason: str) -> None:
            if verdict == VoiceRateLimiter.DENIED_FIRST:
                await _drop(reason)

        # Gate order. The three cheap rejections come BEFORE the room's
        # event budget: events that were never going to be honoured must not
        # spend the budget of the call in progress — after a long disconnect
        # the replayed backlog (all stale) would otherwise use it up and the
        # live call's call.started would be dropped as rate-limited. Their
        # audit volume does not need the budget: every drop is bounded by
        # the per-room drop-audit budget in _audit_voice_control_drop.
        if ctl is None:
            await _drop("malformed")
            return

        own_mxid = credential.matrix_user_id
        if not sender or sender == own_mxid or sender == self._service_sender_mxid(credential):
            await _drop("non_caller_sender")
            return

        sent_ms = int(getattr(event, "server_timestamp", 0) or 0)
        age_ms = int(time.time() * 1000) - sent_ms if sent_ms else 0
        if sent_ms and age_ms > self.VOICE_CONTROL_MAX_AGE_MS:
            # age_ms goes in the row: a huge or negative value points at
            # clock skew between the homeserver and this host, not at a
            # genuinely old event.
            await _drop("stale", age_ms=age_ms)
            return

        verdict = self._voice_events.check(agent_id, room_id)
        if verdict != VoiceRateLimiter.OK:
            await _drop_limited(verdict, "room_rate_limited")
            return

        if not self.STREAMING_ENABLED:
            # Normally short-circuited at dispatch; kept so the handler is
            # safe to call on its own.
            await _drop("live_voice_disabled")
            return

        known = self._voice_registry.get(agent_id, ctl.rtc_session_id)
        if known is None:
            if self._voice_registry.is_retired(agent_id, room_id, ctl.rtc_session_id):
                # A late event of one of this room's earlier calls. Narra
                # has cleared that session; it must not displace the
                # current call.
                await _drop("session_retired")
                return
            if ctl.kind == KIND_CALL_ENDED:
                # A call we never tracked: nothing to tear down, and Narra
                # has already cleared the session (the ack is optional).
                return
            refusal = await self._admit_voice_session_once(
                credential, room_id, sender, ctl.rtc_session_id
            )
            if refusal == _ADMISSION_RATE_LIMITED_QUIET:
                return
            if refusal:
                await _drop(refusal)
                return
            # learn() returns whichever entry won if another room admitted
            # the same session meanwhile, or None if this room retired it.
            known = self._voice_registry.learn(
                agent_id, ctl.rtc_session_id, room_id, sender
            )
            if known is None:
                await _drop("session_retired")
                return
        if known.room_id != room_id or known.caller_mxid != sender:
            await _drop("session_pinned_elsewhere")
            return

        if ctl.kind == KIND_TURN_CANCELLED:
            if not self._voice_registry.mark_cancelled(
                agent_id, ctl.rtc_session_id, ctl.turn_id or ""
            ):
                await _drop("cancelled_turn_cap")
                return
            await self._tombstone_voice_turn(credential, room_id, ctl)
        elif ctl.kind == KIND_CALL_ENDED:
            self._voice_registry.end(agent_id, ctl.rtc_session_id)

        # The ack is its own task, started as soon as the state above is
        # settled (before the audit write): a slow homeserver send does not
        # hold this handler's slot, and the ack does not wait on our DB.
        self._spawn_voice_task(
            self._send_voice_ack(
                credential, room_id, ctl.kind, ctl.rtc_session_id, ctl.turn_id
            ),
            label=f"ack:{ctl.kind}",
        )
        logger.info(
            f"[matrix:{agent_id}] voice control {ctl.kind} "
            f"(room={room_id}, session={ctl.rtc_session_id}, "
            f"turn={ctl.turn_id or '-'})"
        )
        await self._audit(
            EVENT_VOICE_CONTROL_HANDLED,
            agent_id=agent_id,
            app_id=getattr(credential, "app_id", ""),
            chat_id=room_id,
            sender_id=sender[: self.VOICE_AUDIT_SENDER_MAX],
            details={
                "kind": ctl.kind,
                "rtc_session_id": ctl.rtc_session_id,
                "turn_id": ctl.turn_id or "",
            },
        )

    async def _admit_voice_session_once(
        self,
        credential: NarramessengerCredential,
        room_id: str,
        sender: str,
        rtc_session_id: str,
    ) -> str:
        """Admit an unknown session, sharing one admission among the events
        that arrive for it at the same time.

        The first event of a new session runs the gates; events for the
        SAME session from the SAME room and sender that arrive meanwhile
        await that result instead of each spending an admission slot and an
        ``authorize-event`` call. Returns "" when admitted, else the refusal
        reason (``_ADMISSION_RATE_LIMITED_QUIET`` for a rate-limit denial
        that is not the first of its window and so is not audited).
        """
        key = (credential.agent_id, rtc_session_id, room_id, sender)
        pending = self._voice_admitting.get(key)
        if pending is not None:
            return await asyncio.shield(pending)

        verdict = self._voice_admissions.check(credential.agent_id, room_id)
        if verdict == VoiceRateLimiter.DENIED_FIRST:
            return "new_session_rate_limited"
        if verdict == VoiceRateLimiter.DENIED:
            return _ADMISSION_RATE_LIMITED_QUIET

        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._voice_admitting[key] = future
        try:
            refusal = await self._admit_new_voice_session(
                credential, room_id, sender
            )
        except BaseException:
            # Waiters must not hang, and must not treat a crash as a pass.
            refusal = "admission_failed"
            future.set_result(refusal)
            raise
        else:
            future.set_result(refusal)
        finally:
            self._voice_admitting.pop(key, None)
        return refusal

    async def _admit_new_voice_session(
        self,
        credential: NarramessengerCredential,
        room_id: str,
        sender: str,
    ) -> str:
        """Gate the first control event of a session this process has not seen.

        Same two gates a voice turn passes before it may run: the room must
        be an authoritative 1:1 (routed as dm AND exactly two effective
        members — the voice register is 1:1-only), and Narra's
        ``authorize-event`` must allow this sender in this room.
        Fail-closed on every unknown.

        Returns "" when admitted, else the refusal reason.
        """
        room_kind, member_count = await self._resolve_room_shape(
            room_id, credential
        )
        routed_dm = room_kind == "dm" or (room_kind is None and member_count == 2)
        if not (routed_dm and member_count == 2):
            return f"not_one_to_one(room_kind={room_kind},members={member_count})"
        verdict = await self._authorize_event(
            credential,
            _VoiceControlOrigin(chat_id=room_id, sender_id=sender),
            mentioned=False,
        )
        if not verdict.allow:
            return "sender_not_authorized"
        return ""

    async def _tombstone_voice_turn(
        self,
        credential: NarramessengerCredential,
        room_id: str,
        ctl: VoiceControlEvent,
    ) -> None:
        """Make a cancelled turn unclaimable.

        Claims the turn's durable key on its behalf: a voice input for this
        turn that arrives later (re-send, reordering) then fails
        :meth:`_claim_voice_turn` and never runs. A turn that already
        claimed the key is unaffected here — the registry's cancelled set
        is what stops it if it is still queued.
        """
        if self._dedup_store is None or not ctl.turn_id:
            return
        decision = await self._dedup_store.classify(
            voice_turn_claim_key(room_id, ctl.rtc_session_id, ctl.turn_id),
            0,
            agent_id=credential.agent_id,
        )
        if decision["layer"] == "db_fail_open":
            # The tombstone then lives only in the in-memory layer.
            await self._audit(
                EVENT_DEDUP_FAIL_OPEN,
                agent_id=credential.agent_id,
                app_id=getattr(credential, "app_id", ""),
                chat_id=room_id,
                details={
                    "error": decision.get("error", ""),
                    "context": "voice_turn_tombstone",
                },
            )

    async def _claim_voice_turn(
        self, credential: NarramessengerCredential, message: ParsedMessage
    ) -> bool:
        """Atomically claim a voice turn before any model or tool runs.

        Returns False when the turn must not run: the same logical turn was
        already claimed (Narra re-sent the input under a new event id) or it
        was cancelled first (tombstone). Degraded voice turns (no ids to key
        on) are always True — they keep the event-id dedup the base already
        applied.

        On a successful claim of a turn that belongs to a known v15 call,
        the ``turn.started`` ack is sent (off this path — receipt must not
        wait for the model, nor the model for the ack).
        """
        binding = voice_reply_binding(rtc_voice_stamp(message))
        if binding is None:
            return True
        agent_id = getattr(credential, "agent_id", "")
        session_id = binding["rtc_session_id"]
        turn_id = binding["turn_id"]

        if self._dedup_store is not None:
            decision = await self._dedup_store.classify(
                voice_turn_claim_key(message.chat_id, session_id, turn_id),
                0,
                agent_id=agent_id,
            )
            if decision["layer"] == "db_fail_open":
                # The durable layer is down and the claim was let through
                # (failing closed would mute every call for the length of a
                # DB blip). The at-most-once guarantee does not hold for
                # this turn; leave a row that says so. Written off this
                # path: the DB that just failed would stall the caller's
                # first audio for another timeout.
                self._spawn_voice_task(
                    self._audit(
                        EVENT_DEDUP_FAIL_OPEN,
                        message_id=message.message_id,
                        agent_id=agent_id,
                        app_id=getattr(credential, "app_id", ""),
                        chat_id=message.chat_id,
                        details={
                            "error": decision.get("error", ""),
                            "context": "voice_turn_claim",
                            "turn_id": turn_id,
                        },
                    ),
                    label="audit:claim_fail_open",
                )
            if not decision["accept"]:
                logger.info(
                    f"[matrix:{agent_id}] DROP: voice turn already claimed "
                    f"or cancelled (room={message.chat_id}, "
                    f"session={session_id}, turn={turn_id}, "
                    f"layer={decision['layer']})"
                )
                await self._audit(
                    EVENT_INGRESS_DROPPED_DEDUP,
                    message_id=message.message_id,
                    agent_id=agent_id,
                    app_id=getattr(credential, "app_id", ""),
                    chat_id=message.chat_id,
                    sender_id=message.sender_id,
                    details={"dedup_layer": "voice_turn", "turn_id": turn_id},
                )
                return False

        if self._voice_registry.matches(
            agent_id, session_id, message.chat_id, message.sender_id
        ):
            self._spawn_voice_task(
                self._send_voice_ack(
                    credential,
                    message.chat_id,
                    KIND_TURN_STARTED,
                    session_id,
                    turn_id,
                ),
                label="ack:turn.started",
            )
        return True

    async def _drop_dead_voice_turns(
        self, credential: NarramessengerCredential, batch: list
    ) -> list:
        """Remove queued turns that were cancelled, or whose call ended or
        was replaced by the room's next call, while they waited.

        A queued turn has already been claimed, so the tombstone cannot
        stop it; the registry's record does. Without this a cancelled turn
        would run after the current one, and its transcript would be merged
        into the next turn's prompt — the room's queue is shared by
        consecutive calls. Each removal is audited: the turn was accepted
        and then never answered, which must be explainable later.
        """
        agent_id = getattr(credential, "agent_id", "")
        alive = []
        for entry in batch:
            message = entry[0]
            binding = voice_reply_binding(rtc_voice_stamp(message))
            if binding is None or not self._voice_registry.turn_is_dead(
                agent_id, message.chat_id, binding["rtc_session_id"], binding["turn_id"]
            ):
                alive.append(entry)
                continue
            logger.info(
                f"[matrix:{agent_id}] DROP: queued voice turn was cancelled "
                f"or its call is over (room={message.chat_id}, "
                f"turn={binding['turn_id']})"
            )
            await self._audit(
                EVENT_INGRESS_DROPPED_DEDUP,
                message_id=message.message_id,
                agent_id=agent_id,
                app_id=getattr(credential, "app_id", ""),
                chat_id=message.chat_id,
                sender_id=message.sender_id,
                details={
                    "dedup_layer": "voice_turn_cancelled",
                    "turn_id": binding["turn_id"],
                },
            )
        return alive

    async def _send_voice_ack(
        self,
        credential: NarramessengerCredential,
        room_id: str,
        kind: str,
        rtc_session_id: str,
        turn_id: Optional[str],
    ) -> bool:
        """Send one ``ack.v2`` from the agent's own Matrix identity.

        The transaction id is deterministic, so acking the same logical
        event twice produces no second room event. Never raises; a failure
        is logged and audited. A lost ack is NOT reliably retried by the
        other side (Narra re-sends with a fixed transaction id, which the
        homeserver may collapse into the original event), so the outcome of
        a failure here is simply a missing ack: for ``call.started`` that
        leaves the call on v14 reply matching.
        """
        content = build_voice_ack(kind, rtc_session_id, turn_id)
        txn_id = voice_ack_txn_id(kind, rtc_session_id, turn_id)
        last_error = ""
        for attempt in range(1, self.VOICE_ACK_MAX_ATTEMPTS + 1):
            try:
                await matrix_send_event(
                    homeserver=credential.matrix_homeserver_url,
                    token=credential.matrix_access_token,
                    room_id=room_id,
                    event_type=VOICE_ACK_EVENT_TYPE,
                    content=content,
                    txn_id=txn_id,
                    timeout_seconds=self.VOICE_ACK_TIMEOUT_SECONDS,
                )
                return True
            except MatrixSendError as e:
                last_error = f"{e.code}: {e}"
                if attempt < self.VOICE_ACK_MAX_ATTEMPTS:
                    await asyncio.sleep(self.VOICE_ACK_RETRY_DELAY_SECONDS)
        logger.warning(
            f"[matrix:{credential.agent_id}] voice ack {kind} failed "
            f"(room={room_id}, session={rtc_session_id}): {last_error}"
        )
        await self._audit(
            EVENT_TRANSPORT_SEND_FAILED,
            agent_id=credential.agent_id,
            app_id=getattr(credential, "app_id", ""),
            chat_id=room_id,
            details={
                "error_code": "voice_ack_failed",
                "error_message": last_error[:400],
                "attempts": self.VOICE_ACK_MAX_ATTEMPTS,
                "ack_of": kind,
            },
        )
        return False
