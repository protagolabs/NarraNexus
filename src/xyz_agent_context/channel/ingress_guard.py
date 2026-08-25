"""
@file_name: ingress_guard.py
@author:
@date: 2026-08-24
@description: Channel-agnostic ingress circuit breaker — "is this message
worth processing at all?"

Written for the 2026-08-17 ingress design after the 8/14 DM ping-pong
incident (70+ hours, 66k messages, monitoring entirely green). The
inbound path had message-IDENTITY dedup and burst merging, but nothing
that looked at what was actually arriving: every message ran the full
pipeline (narrative retrieval, persona update, agent loop, reply
decision). When the far side is a broken agent reciting the same line,
every layer is individually correct and the whole is a perpetual motion
machine.

This guard is the L2/L3 half of that design — a HARD breaker. The L0
observation tier and L1 merge tier land with the unified ingress in the
IM/Bus refactor. Their absence is covered by making the entry condition
conservative rather than by shipping a lenient breaker: a session must be
BOTH fast AND repetitive. Normal high-frequency conversation — a user
firing off six thoughts, a busy group at peak, a job batch — varies its
content and is structurally immune, which is what the L0 observation
window was there to protect.

Relationship to the two breakers that already exist:
  - ``channel_trigger_base``'s fast-death breaker → "my credential is
    broken, stop restarting the subscriber"
  - ``agent_framework/loop/circuit_breaker`` → "my turns keep failing,
    stop scheduling them"
  - **this one** → "the traffic arriving is not worth processing"
All three share the escalate-cooldown-then-half-open paradigm; none
subsumes another.

Binding rules #14/#15: this gates INGRESS. It never caps, cancels, or
time-limits a running ``agent_loop``, and it never judges the agent's own
model, verbosity, or output — only the shape of inbound traffic.

Clock: one clock, wall-clock, injectable. The credential breaker can use
``time.monotonic()`` because it is memory-only; a cooldown that must
survive a restart has to be expressed in wall time, and running two
clocks in one state machine is how off-by-one-restart bugs are born.
Callers omit ``now``; tests pass it explicitly so every temporal
assertion is arithmetic rather than sleeping.
"""

from __future__ import annotations

import hashlib
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Deque, Dict, Optional, Tuple

from loguru import logger

from xyz_agent_context.schema.channel_ingress_breaker_schema import session_key
from xyz_agent_context.utils.timezone import utc_now

# ── Defaults ──────────────────────────────────────────────────────────
# A session must clear BOTH the rate bar and the repetition bar. Agent
# peers get tighter numbers on both: A2A rooms are where the loops live,
# and no human types the same sentence twenty times in ten minutes.
DEFAULT_WINDOW_SECONDS = 600.0
DEFAULT_RATE_THRESHOLD = 60
DEFAULT_DUP_RATIO_THRESHOLD = 0.8
DEFAULT_AGENT_RATE_THRESHOLD = 20
DEFAULT_AGENT_DUP_RATIO_THRESHOLD = 0.5

# 5min → 30min → 2h → 24h, last step repeating forever. Deliberately a
# literal schedule rather than ``utils/backoff.compute_cooldown_seconds``
# (base·2^(n-1)) — these four numbers come from the design doc and no
# doubling formula produces them.
DEFAULT_SCHEDULE_SECONDS: Tuple[float, ...] = (300.0, 1800.0, 7200.0, 86400.0)

# How many consecutive clean windows walk the tier back down one step.
DEFAULT_RECOVERY_WINDOWS = 2

# Hard ceiling on the per-session event ring. A session at the ceiling has
# already tripped many times over; the cap only stops a pathological
# sender from growing the deque without bound between evictions.
_MAX_EVENTS_PER_SESSION = 4096


def content_fingerprint(chat_id: str, sender_id: str, content: str) -> str:
    """Stable identity of a message's CONTENT within one conversation.

    Scoped by ``(chat_id, sender_id)`` so "ok" from two different people
    in two different rooms are three different things. Empty content
    yields an empty fingerprint, which the guard treats as *unique*, not
    as a repeat — a caption-less file upload must never read as a
    verbatim-repeat storm (same carve-out the empty-content guard makes
    for ``raw["attachment_refs"]``).

    Exact-match today. Near-duplicate detection (simhash) is a later
    upgrade; verbatim recital is what the 8/14 incident actually was, and
    an exact hash catches it with zero false positives.

    This is the unconditional form. ``ChannelTriggerBase._content_fingerprint``
    wraps it with the ``CONTENT_DEDUP_WINDOW_SECONDS`` opt-in that governs
    the dedup-store layer — that gate is about a different question and
    stays where it is.
    """
    if not content:
        return ""
    material = f"{chat_id}|{sender_id}|{content}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class IngressVerdict:
    """One decision, plus the evidence behind it.

    The evidence fields are not decoration: every drop and every
    transition has to be answerable from the DB later (incident lessons
    #3/#5), so the caller writes these straight into the audit row rather
    than re-deriving numbers the guard already computed.
    """

    admit: bool
    session_key: str
    tier: int = 0
    reason: str = "ok"
    # None for the ordinary case. "tripped" / "escalated" / "probe" /
    # "recovered" mark the four moments worth an audit row and a log line.
    transition: Optional[str] = None
    window_count: int = 0
    dup_ratio: float = 0.0
    cooldown_seconds: float = 0.0
    suppressed: int = 0
    is_agent_peer: bool = False

    def audit_details(self) -> Dict[str, Any]:
        """The ``details`` payload for a ``channel_trigger_audit`` row."""
        return {
            "session_key": self.session_key,
            "tier": self.tier,
            "reason": self.reason,
            "transition": self.transition,
            "window_count": self.window_count,
            "dup_ratio": round(self.dup_ratio, 3),
            "cooldown_seconds": self.cooldown_seconds,
            "suppressed": self.suppressed,
            "is_agent_peer": self.is_agent_peer,
        }


@dataclass
class _SessionState:
    """In-memory half of one session key's state.

    The sliding window lives here and ONLY here. Persisting a row per
    inbound message would be write amplification for data that expires in
    ten minutes; what goes to the DB is the tier transition, which happens
    a handful of times in a session's life.
    """

    events: Deque[Tuple[datetime, str]] = field(default_factory=deque)
    tier: int = 0
    cooldown_until: Optional[datetime] = None
    suppressed: int = 0
    # Start of the current clean streak — the anchor for tier decay.
    clean_since: Optional[datetime] = None
    loaded: bool = False


class IngressGuard:
    """Per-conversation ingress breaker, shared by every channel.

    One instance per trigger (and one for the managed-mode coordinator).
    Not a Module — modules stay independent (binding rule #3); this is a
    ``channel/`` facility alongside ``ChannelDedupStore`` and
    ``ChannelDebounceMerger``.
    """

    def __init__(
        self,
        repo: Any = None,
        *,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        rate_threshold: int = DEFAULT_RATE_THRESHOLD,
        dup_ratio_threshold: float = DEFAULT_DUP_RATIO_THRESHOLD,
        agent_rate_threshold: int = DEFAULT_AGENT_RATE_THRESHOLD,
        agent_dup_ratio_threshold: float = DEFAULT_AGENT_DUP_RATIO_THRESHOLD,
        schedule_seconds: Tuple[float, ...] = DEFAULT_SCHEDULE_SECONDS,
        recovery_windows: int = DEFAULT_RECOVERY_WINDOWS,
    ):
        self._repo = repo
        self._window = window_seconds
        self._rate = rate_threshold
        self._dup = dup_ratio_threshold
        self._agent_rate = agent_rate_threshold
        self._agent_dup = agent_dup_ratio_threshold
        self._schedule = schedule_seconds or DEFAULT_SCHEDULE_SECONDS
        self._recovery_windows = max(1, recovery_windows)
        self._sessions: Dict[str, _SessionState] = {}

    # ── Public surface ────────────────────────────────────────────────

    async def admit(
        self,
        *,
        agent_id: str,
        channel: str,
        chat_id: str,
        sender_id: str,
        fingerprint: str,
        is_agent_peer: bool = False,
        now: Optional[datetime] = None,
    ) -> IngressVerdict:
        """Decide whether one inbound message may enter the pipeline.

        Args:
            agent_id: the receiving agent (audit + owner alerting).
            channel: ``ChannelTriggerBase.channel_name``.
            chat_id / sender_id: the conversation and who spoke.
            fingerprint: from ``content_fingerprint``; "" means "no
                content to fingerprint" and is treated as unique.
            is_agent_peer: the far side is another agent — tightens both
                thresholds.
            now: injectable clock. Callers omit it.

        Returns:
            An ``IngressVerdict``. ``admit=False`` means the caller must
            drop the message AND write an audit row — never a silent
            return (lessons #3/#5).
        """
        now = now or utc_now()
        key = session_key(channel, chat_id, sender_id)
        state = self._sessions.get(key)
        if state is None:
            state = _SessionState()
            self._sessions[key] = state
        if not state.loaded:
            await self._load(key, state, now)

        # 1. Still cooling? Drop, count, and say so.
        if state.cooldown_until is not None:
            if now < state.cooldown_until:
                state.suppressed += 1
                return IngressVerdict(
                    admit=False,
                    session_key=key,
                    tier=state.tier,
                    reason="cooling",
                    suppressed=state.suppressed,
                    is_agent_peer=is_agent_peer,
                )
            return await self._half_open(
                key, state, now, agent_id, channel, chat_id, sender_id, is_agent_peer
            )

        # 2. Record and evaluate the window.
        self._record(state, now, fingerprint)
        count, ratio = self._window_stats(state)
        rate_bar = self._agent_rate if is_agent_peer else self._rate
        dup_bar = self._agent_dup if is_agent_peer else self._dup

        if count >= rate_bar and ratio >= dup_bar:
            return await self._trip(
                key, state, now, agent_id, channel, chat_id, sender_id,
                count, ratio, is_agent_peer,
            )

        # 3. Behaving. Walk the escalation memory back down if it has been
        #    behaving long enough.
        transition = await self._maybe_recover(
            key, state, now, agent_id, channel, chat_id, sender_id
        )
        return IngressVerdict(
            admit=True,
            session_key=key,
            tier=state.tier,
            reason="recovered" if transition else "ok",
            transition=transition,
            window_count=count,
            dup_ratio=ratio,
            is_agent_peer=is_agent_peer,
        )

    def open_session_count(self) -> int:
        """Sessions currently carrying escalation memory.

        Isolation is a STANDING state, not just the row written at trip
        time (incident lesson #4) — this feeds ``health_snapshot()`` and
        the heartbeat so a zombie loop cannot burn 70 hours behind an
        all-green dashboard again.
        """
        return sum(1 for s in self._sessions.values() if s.tier > 0)

    def cooling_session_count(self) -> int:
        """Sessions whose ingress is suppressed right now."""
        now = utc_now()
        return sum(
            1
            for s in self._sessions.values()
            if s.cooldown_until is not None and now < s.cooldown_until
        )

    def forget(self, channel: str, chat_id: str, sender_id: str) -> None:
        """Drop a session's in-memory state (credential unbound, room left).

        Deliberately does NOT delete the durable row: memory is a cache of
        the DB here, not the other way round, and a re-bind must not hand
        a re-offender a fresh budget.
        """
        self._sessions.pop(session_key(channel, chat_id, sender_id), None)

    # ── Internals ─────────────────────────────────────────────────────

    async def _load(
        self, key: str, state: _SessionState, now: datetime
    ) -> None:
        """Lazily pull the durable row the first time we see a key.

        One read per session key per process lifetime — the hot path never
        touches the DB again.
        """
        state.loaded = True
        if self._repo is None:
            return
        try:
            row = await self._repo.get(key)
        except Exception as e:  # noqa: BLE001
            # Fail open: the guard is not an authorization gate.
            logger.warning(
                f"IngressGuard: durable load failed for {key} "
                f"({type(e).__name__}: {e}) — continuing memory-only"
            )
            return
        if row is None:
            return
        state.tier = row.tier or 0
        state.suppressed = row.suppressed_count or 0
        cooldown_until = row.cooldown_until
        if cooldown_until is not None:
            if cooldown_until.tzinfo is None:
                cooldown_until = cooldown_until.replace(tzinfo=now.tzinfo)
            if now < cooldown_until:
                state.cooldown_until = cooldown_until

    def _record(self, state: _SessionState, now: datetime, fingerprint: str) -> None:
        state.events.append((now, fingerprint))
        cutoff = now - timedelta(seconds=self._window)
        while state.events and state.events[0][0] < cutoff:
            state.events.popleft()
        while len(state.events) > _MAX_EVENTS_PER_SESSION:
            state.events.popleft()

    def _window_stats(self, state: _SessionState) -> Tuple[int, float]:
        """(message count, duplicate ratio) over the live window.

        ratio = 1 - distinct/count, so 30 identical messages score ~0.97
        and 30 different ones score 0.0. Empty fingerprints each count as
        their own distinct value — see ``content_fingerprint``.
        """
        count = len(state.events)
        if count == 0:
            return 0, 0.0
        distinct = 0
        seen: set[str] = set()
        for idx, (_, fp) in enumerate(state.events):
            if not fp:
                distinct += 1  # unfingerprintable → never a duplicate
                continue
            if fp not in seen:
                seen.add(fp)
                distinct += 1
        return count, 1.0 - (distinct / count)

    async def _trip(
        self,
        key: str,
        state: _SessionState,
        now: datetime,
        agent_id: str,
        channel: str,
        chat_id: str,
        sender_id: str,
        count: int,
        ratio: float,
        is_agent_peer: bool,
    ) -> IngressVerdict:
        was_open = state.tier > 0
        state.tier += 1
        cooldown = self._schedule[min(state.tier - 1, len(self._schedule) - 1)]
        state.cooldown_until = now + timedelta(seconds=cooldown)
        suppressed_before = state.suppressed
        # A fresh isolation absorbs a fresh count, so the number in the
        # alert answers "how much did THIS cooldown swallow?".
        state.suppressed = 0
        state.clean_since = None
        state.events.clear()

        transition = "escalated" if was_open else "tripped"
        reason = "agent_peer_repeat_storm" if is_agent_peer else "repeat_storm"
        logger.error(
            f"IngressGuard {transition}: {key} tier={state.tier} "
            f"count={count} dup_ratio={ratio:.2f} agent_peer={is_agent_peer} "
            f"cooldown={cooldown}s — ingress suppressed for this conversation"
        )
        await self._persist(
            key,
            {
                "channel": channel,
                "agent_id": agent_id,
                "chat_id": chat_id,
                "sender_id": sender_id,
                "tier": state.tier,
                "cooldown_until": state.cooldown_until,
                "suppressed_count": 0,
                "last_reason": reason,
                "last_tripped_at": now,
            },
        )
        return IngressVerdict(
            admit=False,
            session_key=key,
            tier=state.tier,
            reason=reason,
            transition=transition,
            window_count=count,
            dup_ratio=ratio,
            cooldown_seconds=cooldown,
            suppressed=suppressed_before,
            is_agent_peer=is_agent_peer,
        )

    async def _half_open(
        self,
        key: str,
        state: _SessionState,
        now: datetime,
        agent_id: str,
        channel: str,
        chat_id: str,
        sender_id: str,
        is_agent_peer: bool,
    ) -> IngressVerdict:
        """Cooldown elapsed: let exactly one message through as a probe.

        The tier is KEPT — same reasoning as ``_breaker_release`` on the
        credential breaker. A session that clears its cooldown and
        immediately resumes reciting must land on the next step of the
        schedule, not restart at five minutes; otherwise a persistent
        loop oscillates at the cheapest tier forever.
        """
        suppressed = state.suppressed
        state.cooldown_until = None
        state.suppressed = 0
        state.events.clear()
        state.clean_since = now

        logger.info(
            f"IngressGuard probe: {key} tier={state.tier} "
            f"suppressed={suppressed} — admitting one message to re-test"
        )
        await self._persist(
            key,
            {
                "channel": channel,
                "agent_id": agent_id,
                "chat_id": chat_id,
                "sender_id": sender_id,
                "cooldown_until": None,
                "suppressed_count": 0,
                "last_reason": "cooldown_expired",
            },
        )
        return IngressVerdict(
            admit=True,
            session_key=key,
            tier=state.tier,
            reason="cooldown_expired",
            transition="probe",
            suppressed=suppressed,
            is_agent_peer=is_agent_peer,
        )

    async def _maybe_recover(
        self,
        key: str,
        state: _SessionState,
        now: datetime,
        agent_id: str,
        channel: str,
        chat_id: str,
        sender_id: str,
    ) -> Optional[str]:
        """Walk the tier down one step per N clean windows, to zero.

        The design's "逐级降回，最终清零". Without it a conversation that
        misbehaved once in its life would carry a 24h-capable tier
        forever, and the first bad minute a year later would cost a day.
        """
        if state.tier <= 0:
            return None
        if state.clean_since is None:
            state.clean_since = now
            return None
        if (now - state.clean_since).total_seconds() < self._window * self._recovery_windows:
            return None

        state.tier -= 1
        state.clean_since = now
        logger.info(f"IngressGuard recovered: {key} tier={state.tier}")
        await self._persist(
            key,
            {
                "channel": channel,
                "agent_id": agent_id,
                "chat_id": chat_id,
                "sender_id": sender_id,
                "tier": state.tier,
                "cooldown_until": None,
                "last_reason": "recovered",
            },
        )
        return "recovered"

    async def _persist(self, key: str, updates: Dict[str, Any]) -> None:
        """Write-through on transition only. Never raises: losing the
        durable copy costs restart-survival for one session; taking the
        ingress path down would be strictly worse."""
        if self._repo is None:
            return
        try:
            await self._repo.upsert_state(key, updates)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"IngressGuard: durable write failed for {key} "
                f"({type(e).__name__}: {e}) — in-memory state still enforced"
            )
