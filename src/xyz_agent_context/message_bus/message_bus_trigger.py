"""
@file_name: message_bus_trigger.py
@author: NarraNexus
@date: 2026-04-03
@description: Background poller that delivers pending messages to agents

Polls bus_messages table, triggers AgentRuntime for agents with
unprocessed messages.

Design:
- Single poller cycles through all registered agents (from bus_agent_registry)
- Groups pending messages by channel_id (per-channel batching)
- For each channel with pending messages, triggers AgentRuntime.run()
- On success: advances the cursor via ack_processed()
- On failure: records failure via record_failure()

Usage:
    DATABASE_URL=sqlite:///path/to/db uv run python -m xyz_agent_context.message_bus.message_bus_trigger
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from loguru import logger

from xyz_agent_context.agent_framework.llm.failure import (
    MAX_REDACTED_ERROR_LEN,
    is_credential_error,
    redact_secrets,
)
from xyz_agent_context.services.service_audit import ServiceAuditor
from xyz_agent_context.message_bus.local_bus import (
    POISON_FAILURE_THRESHOLD as _POISON_FAILURE_THRESHOLD,
    LocalMessageBus,
    _as_utc,
    canonical_ts,
)
from xyz_agent_context.message_bus.patrol import PATROL_MSG_TYPE
from xyz_agent_context.schema.team_schema import (
    TEAM_ROOM_OWNER_PREFIX,
    USER_SENDER_PREFIX,
)
from xyz_agent_context.message_bus.system_messages import (
    PLATFORM_MSG_TYPES,
    placeholders as _platform_placeholders,
    trigger_label as _platform_trigger_label,
)
from xyz_agent_context.message_bus.schemas import BusMessage
from xyz_agent_context.schema import BUS_TEAM_ROOM_EXTRA_KEY, WorkingSource

# Poll interval in seconds (initial; adaptive bounds below)
POLL_INTERVAL = 3

# Maximum concurrent agent processing workers
MAX_WORKERS = 3

# Rate limiting constants
RATE_LIMIT_MAX = 20
RATE_LIMIT_WINDOW = 1800  # 30 minutes in seconds

# Adaptive polling constants. Kept low so a team group-chat reply lands quickly
# (the trigger is a separate process; this is the latency the user feels after
# an idle period). Worst-case idle latency ≈ POLL_MAX_INTERVAL.
POLL_MIN_INTERVAL = 3
POLL_MAX_INTERVAL = 12
POLL_STEP_UP = 3

# Team group chat: cap how many consecutive agent-to-agent hops can keep the
# @-mention cascade alive without a human message. Past this, an agent reply's
# @mentions are dropped so two agents can't @ each other forever. A user
# message resets the chain.
MAX_TEAM_AGENT_HOPS = 4

# Team group chat: how many recent room messages to feed a triggered agent as
# context (oldest→newest). The agent replies to the latest message addressed to
# it, but SEES the recent scrollback — incl. a shared image/file posted by
# someone else — so it can Read and discuss it without a manual relay. Capped to
# bound the per-turn token cost.
TEAM_HISTORY_LIMIT = 20

# Owned by local_bus (whose `get_pending_messages` enforces the filter) and
# imported here so the two can't drift. Once a message's failure_count reaches
# it, the message is permanently dropped from the pending queue with no further
# retries — see `_notify_permanent_failure` below, which is the only signal the
# owner gets when that happens.
POISON_FAILURE_THRESHOLD = _POISON_FAILURE_THRESHOLD

# De-dup window for permanent-failure inbox notices, keyed per
# (agent_id, error_category). Same window as the rate limiter — a batch of
# messages failing for one root cause (e.g. a broken provider key) should
# not write one inbox row per message.
FAILURE_NOTIFY_COOLDOWN_SECONDS = 1800  # 30 minutes

# Credential-error classification and secret redaction moved to the shared
# ``agent_framework.llm.failure`` module so every background LLM path (bus,
# narrative updater, Step-5 hooks) asks the same questions the same way.
# ``_classify_error`` / ``_redact_error_for_owner`` below delegate to it.
MAX_NOTIFIED_ERROR_LEN = MAX_REDACTED_ERROR_LEN


def im_channel_prefixes() -> tuple[str, ...]:
    """Channel-id prefixes owned by dedicated IM triggers — registry-driven.

    ChannelInboxWriter persists IM turns to ``bus_messages`` under
    ``{channel}_{chat_id}`` purely for history/Inbox display; the channel's
    own trigger already ran AgentRuntime for them. Those rows must never be
    re-dispatched here. The set used to be a hand-maintained tuple
    ("lark_", "telegram_", "slack_") that silently drifted — wechat,
    narramessenger and discord were missing, so every message on those
    channels fired a SECOND agent run wearing the Owner-Relay peer-agent
    prompt (2026-07-03 wechat incident: fabricated context_token sends +
    bogus "我已经在微信上回复你啦" platform DMs). Deriving from
    ``MessageSourceHandler.dedicated_trigger`` keeps a future channel
    covered the moment it registers; computed per call because channel
    modules register at import time and import order isn't guaranteed.
    """
    from xyz_agent_context.channel.message_source_handler import (
        MessageSourceRegistry,
    )

    return tuple(sorted(
        f"{name}_"
        for name, handler in MessageSourceRegistry.handlers().items()
        if handler.dedicated_trigger
    ))


def build_bus_anchor(messages: List[BusMessage]) -> str:
    """Build the clean retrieval anchor for a bus turn.

    The execution prompt (_build_prompt) wraps peer messages in a per-turn
    ~1217-char Owner-Relay boilerplate + From/Time metadata — bus was the only
    real 400 source in prod. The anchor keeps ONLY each peer's body (tagged
    with the sender agent), so the narrative query vector is clean. Oversized
    backlogs are still capped downstream by a length guard.
    See the 2026-06-01 design doc.
    """
    return "\n".join(
        f"[From agent {m.from_agent}] {m.content}" for m in messages
    )


@dataclass
class _InFlight:
    """One dispatched agent turn the poll loop is deliberately not awaiting."""

    task: asyncio.Task
    started_at: float
    # Flipped once the turn actually holds a worker slot. A dispatch that is
    # still False is queued behind `_semaphore`, and the gap between the two
    # counts is exactly the slot-starvation signal the heartbeat reports.
    running: bool = False


class MessageBusTrigger:
    """
    Background poller that processes pending MessageBus messages.

    Finds agents with unprocessed messages and triggers AgentRuntime to
    handle them.

    Args:
        bus: A MessageBusService instance (typically LocalMessageBus).
        poll_interval: Seconds between poll cycles.
        max_workers: Maximum concurrent agent processing tasks.
    """

    def __init__(
        self,
        bus: LocalMessageBus,
        poll_interval: int = POLL_INTERVAL,
        max_workers: int = MAX_WORKERS,
    ) -> None:
        self._bus = bus
        self._poll_interval = poll_interval
        self._max_workers = max_workers
        self._running = False
        self._semaphore = asyncio.Semaphore(max_workers)
        self._rate_counters: Dict[str, List[float]] = {}
        self._current_interval = poll_interval
        # Per-agent serialisation lock. The global ``_semaphore`` caps
        # concurrent agents but does NOT prevent the same agent from
        # being processed twice in parallel — `get_pending_messages`
        # only filters on ``last_processed_at``, which is advanced
        # after ``_invoke_runtime`` returns. AgentRuntime takes minutes
        # for an LLM-heavy turn; the poll loop fires every 10s; without
        # this lock the same bus_message gets handed to AgentRuntime
        # 3+ times. Observed in production (2026-05-12 13:20 — agent
        # processed one msg_4eb528dc three times, burned ~30K tokens).
        self._agent_locks: Dict[str, asyncio.Lock] = {}
        # last `time.monotonic()` a permanent-failure inbox notice was
        # written for a given "agent_id:error_category" key. See
        # `_notify_permanent_failure`.
        self._failure_notify_cooldown: Dict[str, float] = {}
        # In-flight dispatches, agent_id -> _InFlight. The poll loop spawns
        # these and does NOT await them (see `_poll_cycle`), so this is both
        # the "don't dispatch the same agent twice" guard and the raw material
        # for the audit heartbeat.
        self._in_flight: Dict[str, _InFlight] = {}
        # Wakes the poll loop out of its interval sleep on stop().
        self._stop_event = asyncio.Event()
        # L2/L3 observability. This trigger was the only long-running worker
        # without its own auditor: the supervisor's aggregate liveness only
        # proves the asyncio task object still exists, so when the poll loop
        # wedged on 2026-07-27 it reported "running" for 33 hours while zero
        # messages moved. The counters below are what make a wedge visible —
        # a frozen `cycles` means the loop is stuck, a frozen
        # `dispatched_total` alongside a non-zero `candidates` means messages
        # are piling up unserved.
        self.audit = ServiceAuditor("message_bus_trigger")
        self._cycles = 0
        self._dispatched_total = 0
        self._handled_total = 0
        self._last_candidates = 0
        self._last_dispatch_at: Optional[str] = None

    async def start(self) -> None:
        """Start the polling loop with adaptive interval."""
        self._running = True
        self._stop_event.clear()
        logger.info(
            f"MessageBusTrigger started (poll_interval={self._poll_interval}s, "
            f"max_workers={self._max_workers})"
        )
        await self.audit.started({
            "poll_interval": self._poll_interval,
            "max_workers": self._max_workers,
        })

        while self._running:
            try:
                dispatched = await self._poll_cycle()
                if dispatched:
                    self._current_interval = POLL_MIN_INTERVAL
                else:
                    self._current_interval = min(
                        self._current_interval + POLL_STEP_UP,
                        POLL_MAX_INTERVAL,
                    )
            except Exception as e:
                logger.exception(f"MessageBusTrigger poll cycle error: {e}")
                await self.audit.error({"stage": "poll_cycle", "error": repr(e)})

            # Throttled inside ServiceAuditor (60s), so this is cheap per cycle.
            await self.audit.heartbeat(self.liveness_snapshot())
            # Sleep the interval, but wake immediately on stop() — otherwise a
            # SIGTERM waits out up to POLL_MAX_INTERVAL before the loop notices.
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._current_interval
                )
            except asyncio.TimeoutError:
                pass

        await self.audit.stopped(self.liveness_snapshot())

    def stop(self) -> None:
        """Signal the polling loop to stop and drop any in-flight dispatches.

        Cancelling here is shutdown, not a policy on run length: the process is
        going away either way, and leaving the tasks would just leak them past
        the loop that owns them.
        """
        self._running = False
        self._stop_event.set()
        for agent_id, flight in list(self._in_flight.items()):
            flight.task.cancel()
            logger.info(f"MessageBusTrigger: cancelling in-flight turn for {agent_id}")
        logger.info("MessageBusTrigger stopping")

    def liveness_snapshot(self) -> Dict[str, Any]:
        """Work counters for the audit heartbeat (L2 + L3).

        Read this, not "is the process up", to tell a wedged bus from an idle
        one: `cycles` advances whenever the loop is alive, `dispatched_total`
        advances whenever work actually starts, and `candidates` says whether
        there was anything to do. Frozen `cycles` = the loop is stuck. Advancing
        `cycles` with a frozen `dispatched_total` and non-zero `candidates` =
        the loop is fine but nothing can start.

        `running` vs `waiting` is the slot-starvation signal: sustained
        `running == max_workers` with `waiting > 0` means the worker pool, not
        the agents, is the bottleneck.

        `longest_running_s` / `longest_running_agent` are DIAGNOSTIC ONLY —
        they name who is holding a slot so a human can look. Nothing here ever
        force-stops a turn; a multi-hour run is a legitimate workload
        (binding rule #14), and the failure mode this guards against is our own
        loop dying, not an agent taking its time.
        """
        now = time.monotonic()
        running = sum(1 for f in self._in_flight.values() if f.running)
        longest_agent, longest_s = None, 0
        for agent_id, flight in self._in_flight.items():
            if not flight.running:
                continue
            elapsed = int(now - flight.started_at)
            # `is None` first: a turn that started this second has elapsed 0 and
            # must still be named, or a freshly-wedged slot reports as nobody.
            if longest_agent is None or elapsed > longest_s:
                longest_agent, longest_s = agent_id, elapsed
        return {
            "cycles": self._cycles,
            "candidates": self._last_candidates,
            "dispatched_total": self._dispatched_total,
            "handled_total": self._handled_total,
            "running": running,
            "waiting": len(self._in_flight) - running,
            "max_workers": self._max_workers,
            "longest_running_s": longest_s,
            "longest_running_agent": longest_agent,
            "last_dispatch_at": self._last_dispatch_at,
        }

    async def _agents_with_pending(self) -> List[str]:
        """Agents that have at least one message past their cursor.

        One query replacing "every agent that is a member of any channel" —
        364 of them on prod, each of which then ran its own
        ``get_pending_messages`` (plus a poison lookup per row) every few
        seconds just to conclude it had nothing to do.

        Deliberately a CANDIDATE set: it mirrors ``get_pending_messages``'
        cursor + not-self-sent predicate but skips the poison and @mention
        filters, which stay in ``_process_agent`` where the real decision is
        made. Over-including is free; under-including would drop a message.
        """
        rows = await self._bus._db.execute(
            "SELECT DISTINCT cm.agent_id AS agent_id "
            "FROM bus_channel_members cm "
            "JOIN bus_messages m ON m.channel_id = cm.channel_id "
            "WHERE m.created_at > COALESCE(cm.last_processed_at, '1970-01-01') "
            "AND m.from_agent != cm.agent_id",
            (),
        )
        return [r["agent_id"] for r in rows] if rows else []

    def _dispatch(self, agent_id: str) -> None:
        """Spawn a supervised turn for one agent and return immediately."""
        task = asyncio.create_task(self._run_dispatch(agent_id))
        self._in_flight[agent_id] = _InFlight(task=task, started_at=time.monotonic())
        # Paired done-callback: an unawaited task's exception would otherwise
        # surface only as a GC warning (incident lesson #2).
        task.add_done_callback(lambda t, a=agent_id: self._on_dispatch_done(a, t))

    async def _run_dispatch(self, agent_id: str) -> None:
        handled = await self._process_agent(agent_id)
        if handled:
            self._handled_total += 1

    def _on_dispatch_done(self, agent_id: str, task: asyncio.Task) -> None:
        self._in_flight.pop(agent_id, None)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.exception(
                f"MessageBusTrigger: dispatch for {agent_id} died: {exc!r}",
                exc_info=exc,
            )

    async def _poll_cycle(self) -> int:
        """Run one poll cycle. Returns how many turns it dispatched.

        The cycle **does not await the work it starts**. It used to
        ``asyncio.gather`` every agent and wait for all of them, which meant a
        single coroutine that never returned — an LLM connection wedged with no
        timeout, holding one of the ``max_workers`` slots — froze the entire
        loop. That is exactly what happened on prod 2026-07-27: 33 hours with
        zero messages processed for anyone, no exception, no restart, while
        liveness still read "running".

        Now a stuck turn holds its own task and its own slot; the loop keeps
        cycling, the heartbeat keeps reporting, and `_in_flight` names the
        agent that is stuck.
        """
        candidates = await self._agents_with_pending()
        self._cycles += 1
        self._last_candidates = len(candidates)

        # The patrol lane. A second candidate source on the same cycle: teams
        # whose board has unfinished work and whose lead is due for a sweep.
        # Runs through the same semaphore and the same per-agent lock as
        # message dispatch, so a patrol can never double-run a lead that is
        # already busy — and an empty board yields no candidates at all, which
        # is the feature's whole cost guarantee.
        dispatched_patrols = await self._dispatch_patrols()

        if not candidates:
            return dispatched_patrols

        dispatched = 0
        for agent_id in candidates:
            # Its previous turn is still going; the per-agent lock would make a
            # second dispatch wait, but not spawning it at all is cheaper and
            # keeps `_in_flight` meaning one entry per agent.
            if agent_id in self._in_flight:
                continue
            self._dispatch(agent_id)
            dispatched += 1

        if dispatched:
            self._dispatched_total += dispatched
            self._last_dispatch_at = datetime.now(timezone.utc).isoformat()
        return dispatched + dispatched_patrols

    async def _dispatch_patrols(self) -> int:
        """Wake the leads whose boards are due for a sweep. Returns how many.

        Failures are swallowed: the patrol lane is an addition to the poll
        cycle, and a bad sweep must never cost the room its ordinary message
        delivery.
        """
        try:
            from xyz_agent_context.message_bus.patrol import teams_due_for_patrol
            from xyz_agent_context.utils.db.db_factory import get_db_client

            due = await teams_due_for_patrol(await get_db_client())
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[patrol] candidate sweep failed: {e}")
            return 0

        from xyz_agent_context.agent_framework.loop.circuit_breaker import should_skip
        from xyz_agent_context.message_bus.patrol import mark_patrolled

        count = 0
        for team_id, lead_agent_id, channel_id in due:
            # Its own turn is still going — a patrol would just queue behind the
            # per-agent lock, and skipping is cheaper than holding a slot.
            if lead_agent_id in self._in_flight:
                continue
            # Same gate message dispatch uses. Without it a lead with a dead key
            # or exhausted quota gets woken every 180-600s, forever, to run a
            # turn that cannot succeed — the exact loop the breaker exists to
            # stop, entered through a lane that did not ask it.
            # No try/except: `should_skip` already fails open internally
            # (returns (False, None) on any read error), so wrapping it would
            # be a handler that can never run — and dead handlers read as
            # "this can throw", which is worse than none.
            cb_skip, cb_reason = await should_skip(lead_agent_id)
            if cb_skip:
                logger.info(
                    f"[patrol] skipping {lead_agent_id} (circuit-breaker: {cb_reason})"
                )
                # The cursor still moves: leaving it stale would make this team
                # a candidate on every single cycle for as long as it is broken.
                try:
                    await mark_patrolled(await get_db_client(), team_id)
                except Exception as e:  # noqa: BLE001
                    logger.debug(f"[patrol] cursor stamp failed for {team_id}: {e}")
                continue
            self._dispatch_patrol(team_id, lead_agent_id, channel_id)
            count += 1
        return count

    def _dispatch_patrol(self, team_id: str, lead_agent_id: str, channel_id: str) -> None:
        """Spawn one patrol sweep, gated exactly like a message dispatch.

        Same per-agent lock and same semaphore as ``_process_agent``: a lead
        that is already answering somebody must not be woken a second time to
        patrol, and patrols must not escape the worker cap. Registering in
        ``_in_flight`` is what makes the next cycle skip this lead — and what
        makes a stuck patrol visible in the heartbeat like any other turn.
        """

        async def _guarded() -> None:
            lock = self._agent_locks.setdefault(lead_agent_id, asyncio.Lock())
            async with lock, self._semaphore:
                # Same bookkeeping as `_process_agent`: liveness_snapshot's
                # starvation check and `longest_running_agent` both count only
                # `running` entries, so a patrol that never sets it would hold a
                # worker slot while the heartbeat reported it as merely waiting
                # — the 2026-07-27 shape (33 h of nothing, liveness still green)
                # one lane over.
                flight = self._in_flight.get(lead_agent_id)
                if flight is not None:
                    flight.running = True
                await self._run_patrol(team_id, lead_agent_id, channel_id)

        task = asyncio.create_task(_guarded())
        self._in_flight[lead_agent_id] = _InFlight(
            task=task, started_at=time.monotonic()
        )
        # Never a bare create_task: an exception in a fire-and-forget task is
        # only reported during GC (incident lesson #2).
        task.add_done_callback(
            lambda t, a=lead_agent_id: self._on_dispatch_done(a, t)
        )

    def _should_process_message(
        self, msg: BusMessage, agent_id: str, channel_type: str, channel_owner: str,
    ) -> bool:
        """Check if a message should trigger processing for an agent.

        Rules:
        - Never process own messages
        - DM (direct) channels: always process
        - Group channels:
            * Channel owner (created_by) is ALWAYS activated by any new message
            * Other members: only process if mentioned or @everyone
        """
        if msg.from_agent == agent_id:
            return False
        if channel_type == "direct":
            return True
        # Channel owner is always activated, regardless of mentions
        if agent_id == channel_owner:
            return True
        if not msg.mentions:
            return False
        return agent_id in msg.mentions or "@everyone" in msg.mentions

    async def _get_channel_info(self, channel_id: str) -> tuple[str, str]:
        """Get (channel_type, created_by) for a channel."""
        # get_one builds dialect-correct SQL per backend. ``self._bus._db`` is
        # the RAW backend (LocalMessageBus is handed db._backend, not the
        # AsyncDatabaseClient wrapper), so the raw ``execute`` path takes the
        # query verbatim with NO %s→? translation — a MySQL `%s` placeholder
        # threw `near "%"` on SQLite and silently broke bus delivery for every
        # agent that had channel messages (2026-06-09: 影/镜 never received 零's
        # messages). get_one sidesteps the placeholder problem entirely.
        row = await self._bus._db.get_one("bus_channels", {"channel_id": channel_id})
        if row:
            return (
                row.get("channel_type", "group"),
                row.get("created_by", ""),
            )
        return ("group", "")

    def _check_rate_limit(self, agent_id: str, channel_id: str) -> bool:
        """Return True if within rate limit, False if exceeded."""
        key = f"{agent_id}:{channel_id}"
        now = time.monotonic()
        timestamps = self._rate_counters.get(key, [])
        timestamps = [t for t in timestamps if now - t < RATE_LIMIT_WINDOW]
        if len(timestamps) >= RATE_LIMIT_MAX:
            logger.warning(
                f"Rate limit exceeded for {agent_id} in channel {channel_id} "
                f"({len(timestamps)}/{RATE_LIMIT_MAX} in {RATE_LIMIT_WINDOW}s)"
            )
            return False
        timestamps.append(now)
        self._rate_counters[key] = timestamps
        return True

    async def _process_agent(self, agent_id: str) -> bool:
        """Process pending messages for an agent. Returns True if messages handled.

        Acquires a per-agent lock so a slow ``_invoke_runtime`` does not let
        the next poll fire a second AgentRuntime for the same pending
        message. See ``__init__`` for the production incident this guards.
        """
        # Circuit-breaker skip-gate: a paused (dead key / quota) or cooling
        # agent is skipped entirely — its pending messages are left queued
        # (NOT acked), so they are handled once it resumes. This is what
        # frees the bus from re-triggering a broken agent every poll. Checked
        # before the semaphore so a paused agent doesn't hold a slot.
        #
        # Accepted trade-off: while an agent stays paused (owner hasn't fixed
        # the key yet), its channel backlog accumulates and is drained in one
        # burst on resume. That's intended — dropping/ack'ing messages for a
        # temporarily-broken agent would be silent data loss; the backlog
        # converges once the owner reconfigures and the breaker re-arms.
        from xyz_agent_context.agent_framework.loop.circuit_breaker import should_skip
        cb_skip, cb_reason = await should_skip(agent_id)
        if cb_skip:
            logger.debug(
                f"MessageBusTrigger: skipping agent {agent_id} "
                f"(circuit-breaker: {cb_reason})"
            )
            return False

        lock = self._agent_locks.setdefault(agent_id, asyncio.Lock())
        async with lock, self._semaphore:
            # Slot acquired — from here the turn counts as `running` rather
            # than `waiting` in the heartbeat. Absent when `_process_agent` is
            # called directly (tests), which is why this is a lookup, not an
            # assumption.
            flight = self._in_flight.get(agent_id)
            if flight is not None:
                flight.running = True
            try:
                pending = await self._bus.get_pending_messages(agent_id)
                if not pending:
                    return False

                by_channel: Dict[str, List[BusMessage]] = defaultdict(list)
                for msg in pending:
                    by_channel[msg.channel_id].append(msg)

                handled_any = False
                for channel_id, messages in by_channel.items():
                    # Skip IM-channel-owned channels — each has its own dedicated
                    # trigger that already processed the message; re-consuming
                    # would fire AgentRuntime a second time and send duplicate
                    # replies. Prefixes derive from MessageSourceRegistry (see
                    # im_channel_prefixes) so new channels can't be forgotten.
                    if channel_id.startswith(im_channel_prefixes()):
                        latest = max(messages, key=lambda m: str(m.created_at))
                        await self._bus.ack_processed(agent_id, channel_id, latest.created_at)
                        continue

                    channel_type, channel_owner = await self._get_channel_info(channel_id)

                    # Mention filtering (channel owner is always activated)
                    relevant = [
                        m for m in messages
                        if self._should_process_message(m, agent_id, channel_type, channel_owner)
                    ]
                    if not relevant:
                        # Still ack to advance cursor
                        latest = max(messages, key=lambda m: str(m.created_at))
                        await self._bus.ack_processed(
                            agent_id, channel_id, latest.created_at
                        )
                        continue

                    # Rate limiting
                    if not self._check_rate_limit(agent_id, channel_id):
                        latest = max(relevant, key=lambda m: str(m.created_at))
                        await self._bus.ack_processed(
                            agent_id, channel_id, latest.created_at
                        )
                        continue

                    trigger_msg = relevant[-1]
                    await self._handle_channel_batch(
                        agent_id, channel_id, relevant, trigger_msg, channel_owner
                    )
                    handled_any = True

                return handled_any
            except Exception as e:
                logger.exception(
                    f"MessageBusTrigger: error processing agent {agent_id}: {e}"
                )
                return False

    async def _get_agent_owner(self, agent_id: str) -> Optional[str]:
        """Look up the owner user_id for an agent. Returns "" when the agent
        is unknown and None when the LOOKUP failed (resolve_owner's split) —
        every consumer gates on truthiness, so both degrade the same way.
        Delegates to the shared AgentRepository.resolve_owner seam."""
        try:
            from xyz_agent_context.repository.agent_repository import AgentRepository
            from xyz_agent_context.utils.db.db_factory import get_db_client
            return await AgentRepository(await get_db_client()).resolve_owner(agent_id)
        except Exception as e:
            logger.warning(f"_get_agent_owner({agent_id}) failed: {e}")
            return ""

    async def _ack_room_seen(
        self, agent_id: str, channel_id: str, trigger_message: BusMessage,
        is_team: bool, rendered_from: Optional[str],
    ) -> None:
        """Advance the READ cursor for a team room whose turn actually ran.

        A team room delivers by RENDERING: `_build_team_prompt` puts the room's
        recent scrollback into the turn's user message, so what the agent has
        been shown is exactly that window. That is what "read" means here, and
        it holds whether the agent replied or stayed silent — silence is a
        reply-discipline decision, not a claim that it did not look.

        `rendered_from` is the oldest message the prompt actually carried. The
        cursor may only pass it when the window reaches back to wherever the
        cursor already was — otherwise there is a GAP of messages this turn
        never showed, and a single high-water cursor cannot say "read the window
        but not the gap below it". It would swallow them.

        That gap is reachable and not rare: a member nobody @mentions for a
        while accumulates a backlog, and the day it finally gets @mentioned the
        prompt still only carries `TEAM_HISTORY_LIMIT` messages. Advancing to
        the trigger would mark the rest read having never rendered them — the
        same silent loss this method refuses to cause at the un-mentioned and
        rate-limited ack sites, arriving through the one path that does run a
        turn.

        So on a gap the cursor simply does not move. The room stays behind and
        keeps surfacing through the unread list, which is where an un-caught-up
        member is supposed to see it. Rooms whose backlog fits the window — the
        ordinary case — still converge in one turn.

        Nothing else advanced this cursor. Its only other writer keys off a bus
        delivery tool showing up in the turn's trace, and a team reply is posted
        by this trigger — server-side, no tool call — so the cursor sat at
        `joined_at` for the life of the agent while every team message stayed
        unread and rode into EVERY scenario's context, owner chat included.

        Deliberately NOT called from the two ack sites in `_process_agent`
        (un-mentioned, rate-limited). Those advance `last_processed_at` without
        running a turn, so nothing was rendered and nothing was seen. Marking
        them read would drop the messages unseen — and would take with them the
        only way a member nobody @mentioned ever learns what its room is doing,
        which is a capability, not an oversight.

        Team rooms only. In a DM the unread list IS the queue: "I will get to
        it" depends on the message resurfacing, so only an actual reply clears
        it and that path runs through the module hook.

        Best-effort — a cursor that fails to advance costs some duplicate
        context next turn; raising here would cost the turn itself.
        """
        if not is_team or not rendered_from:
            return
        try:
            # "Did the window reach the bottom of what this agent still owes?"
            # `get_unread` already measures against the cursor, so anything it
            # returns below the window is by definition a message this turn did
            # not render. One question, correct whether or not a cursor exists.
            floor = canonical_ts(rendered_from)
            behind = [
                m for m in await self._bus.get_unread(agent_id)
                if m.channel_id == channel_id and canonical_ts(m.created_at) < floor
            ]
            if behind:
                logger.info(
                    f"[bus] read cursor held for {agent_id} in {channel_id}: "
                    f"{len(behind)} unread message(s) predate this turn's "
                    f"scrollback and were never rendered"
                )
                return
            await self._bus.ack_read(
                agent_id=agent_id,
                channel_id=channel_id,
                up_to_timestamp=trigger_message.created_at,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[bus] could not advance read cursor for {agent_id} "
                f"in {channel_id}: {e}"
            )

    async def _handle_channel_batch(
        self,
        agent_id: str,
        channel_id: str,
        messages: List[BusMessage],
        trigger_message: BusMessage,
        channel_owner: str = "",
    ) -> None:
        """
        Handle a batch of messages from a single channel for an agent.

        Builds a prompt, invokes AgentRuntime, and on success advances the
        processing cursor. On failure, records the failure for retry tracking.

        Team group chat (``channel_owner`` is the synthetic ``team_<id>``
        marker) is a distinct surface: the agent gets a group-chat prompt
        (not the owner-relay), and its reply is posted BACK INTO the channel
        — with any @mentions parsed so teammates get pulled in — so the user
        and teammates all see it in the shared room. Every other channel
        (peer DM, IM bridges) keeps the original owner-relay + inbox path.
        """
        # Imported here, not at module scope: `agent_runtime` pulls in
        # `module`, which imports back into this package — the same circular
        # import that keeps `get_agent_runtime_client` local to
        # `_invoke_runtime`.
        from xyz_agent_context.agent_runtime.cancel_watcher import get_cancel_watcher
        from xyz_agent_context.agent_runtime.cancellation import (
            CancellationToken,
            CancelledByUser,
        )

        is_team = channel_owner.startswith(TEAM_ROOM_OWNER_PREFIX)
        # The oldest message the prompt ends up carrying. Declared here, not in
        # the team branch, so the cancelled handler can read it however the body
        # exits — the read cursor may not pass what was never rendered.
        rendered_from: Optional[str] = None
        member_map: Dict[str, str] = {}
        # DM branch overwrites this with the classifier's verdict; team rooms
        # keep False (they never carry the Owner-Relay/Answer-the-peer split).
        errand_continuation = False

        # Hop timing ([bus-timing], 2026-08-05): the 2026-08-01 event clocked
        # a bus hop at 45-95s with no way to split "sat in the queue" from
        # "the turn itself". queue_wait = TRIGGER message insert -> this
        # dispatch (bounded by the adaptive poll, 3-12s; the trigger is the
        # NEWEST batched message, so this is a lower bound on user-perceived
        # wait — oldest_wait is the upper bound); turn = the runtime call;
        # hop closes when the reply is DELIVERED (team room: our post below;
        # DM: the agent's own bus_send fires mid-turn, so turn covers it).
        # Companion of the runtime's [turn-timing] line, which splits the
        # turn body further. Measurement first — Base recvrdLPavdQgU.
        # Timestamp parsing goes through the bus package's own _as_utc —
        # the one parser every bus timestamp comparison already uses.
        _t_dispatch = time.monotonic()
        _now_utc = datetime.now(timezone.utc)

        def _wait_s(raw) -> float:
            parsed = _as_utc(raw)
            return (
                max(0.0, (_now_utc - parsed).total_seconds())
                if parsed else -1.0
            )

        _queue_wait_s = _wait_s(trigger_message.created_at)
        _oldest_wait_s = max(
            (_wait_s(m.created_at) for m in messages), default=_queue_wait_s
        )
        _turn_s = -1.0
        _hop_done = False
        try:
            if is_team:
                member_map = await self._team_member_names(channel_id)
                team_owner = await self._get_agent_owner(agent_id)
                team_id = channel_owner[len(TEAM_ROOM_OWNER_PREFIX):]
                # Feed the recent room scrollback (not just the @mention batch)
                # so the agent sees a shared file/image posted earlier by anyone
                # and can Read it — no manual relay. `messages` (the @mentions
                # for THIS agent) still marks what it should respond to.
                history = await self._bus.get_recent_messages(channel_id, limit=TEAM_HISTORY_LIMIT)
                bulletin = await self._load_bulletin(team_id)
                rendered_from = history[0].created_at if history else None
                lead_agent_id, work_items = await self._team_board(team_id)
                prompt = self._build_team_prompt(
                    agent_id, history, member_map,
                    owner_user_id=team_owner, team_id=team_id,
                    trigger_messages=messages,
                    bulletin=bulletin,
                    lead_agent_id=lead_agent_id,
                    work_items=work_items,
                )
            else:
                # Owner lookup up-front — used by both the prompt (to remind the
                # agent its owner is waiting in chat) and the inbox writer.
                owner_user_id = await self._get_agent_owner(agent_id)
                # Resolve the owner's human name for the relay prose (the raw
                # user_id stays as the send_message_to_user_directly routing key).
                owner_name = ""
                if owner_user_id:
                    from xyz_agent_context.utils.db.db_factory import get_db_client
                    from xyz_agent_context.repository import UserRepository
                    owner_name = await UserRepository(await get_db_client()).get_display_name(owner_user_id)

                # Which directive applies depends on who started this thread:
                # a reply to OUR errand goes to the owner; a fresh question
                # from a peer must be answered on the bus. Getting this wrong
                # is what made recipients answer their owner and leave the
                # asking agent hanging (P1 2026-08-03).
                i_started = await self._incoming_is_reply_to_my_errand(
                    agent_id, channel_id, messages
                )
                errand_continuation = i_started

                # Build prompt from messages
                prompt = self._build_prompt(
                    messages,
                    owner_user_id=owner_user_id,
                    owner_name=owner_name,
                    i_started_this_exchange=i_started,
                )

            logger.info(
                f"MessageBusTrigger: triggering agent {agent_id} "
                f"for channel {channel_id} ({len(messages)} messages, team={is_team})"
            )

            # Team rooms mirror live "what is this agent doing" into
            # bus_agent_activity so the team-chat UI can show running/phase/
            # elapsed/steps (the bus path has no WS stream). Only for team
            # channels. `turn()` owns the start row, the timer heartbeat and
            # the idle flip, whichever way the body exits.
            async with contextlib.AsyncExitStack() as stack:
                on_progress = None
                note_event_id = None
                if is_team:
                    from xyz_agent_context.utils.db.db_factory import get_db_client
                    from xyz_agent_context.message_bus import _bus_activity
                    act = await stack.enter_async_context(
                        _bus_activity.turn(await get_db_client(), agent_id, channel_id)
                    )
                    on_progress = act.on_progress
                    note_event_id = act.note_event_id

                # A stop request for this run arrives through the DB (the
                # owner's click lands in the backend process, not here), so the
                # token needs a watcher to fire it. Registration can only
                # happen once the run HAS an id — Step 0 mints it and
                # `on_event_id` is the first moment it exists. Registered for
                # every bus run, not just team ones: the endpoint is
                # run-scoped, so a future surface should be able to stop a
                # peer-DM turn too.
                from xyz_agent_context.utils.db.db_factory import get_db_client

                cancellation = CancellationToken()
                watcher = get_cancel_watcher(await get_db_client())
                watched_run_id: list[str] = [""]
                # Unregister however the body exits — a token left behind
                # keeps the poll loop alive for a run that is gone.
                stack.callback(lambda: watcher.unregister(watched_run_id[0]))

                async def on_event_id(run_id: str) -> None:
                    watched_run_id[0] = run_id
                    watcher.register(run_id, cancellation)
                    if note_event_id is not None:
                        await note_event_id(run_id)

                # Call AgentRuntime. Pass a clean retrieval anchor (peer bodies
                # only, no Owner-Relay boilerplate) for narrative routing — the
                # execution `prompt` is far noisier. See 2026-06-01 design.
                response_text, turn_event_id = await self._invoke_runtime(
                    agent_id=agent_id,
                    sender_agent_id=trigger_message.from_agent,
                    prompt=prompt,
                    channel_id=channel_id,
                    trigger_message_id=trigger_message.message_id,
                    retrieval_anchor=build_bus_anchor(messages),
                    errand_continuation=errand_continuation,
                    on_progress=on_progress,
                    on_event_id=on_event_id,
                    cancellation=cancellation,
                    # Team rooms are the one surface whose prompt tells the
                    # agent its plain text IS the delivered reply (auto-posted
                    # to the room), so NexusPower monologue joins the collected
                    # text. The peer-DM/inbox branch keeps the monologue
                    # private — its prompt makes no such promise.
                    # The tree this turn continues, as stamped on the message
                    # that woke it. Empty for a user's message (this run then
                    # becomes a root) — see the recorder's bind.
                    root_run_id=trigger_message.root_run_id or "",
                    include_monologue=is_team,
                    # The turn's team, for the MCP identity headers — tools
                    # must learn it from the server, never from a model
                    # parameter (see module/_mcp_identity.py).
                    team_id=team_id if is_team else "",
                    # Same fact, module-side consumer: the team room must NOT
                    # advertise bus tools as its reply surface (plain text
                    # auto-posts; the prompt forbids delivery tools), so the
                    # marker rides trigger_extra_data for the expressive
                    # declaration to gate on.
                    team_room=is_team,
                )

            _turn_s = time.monotonic() - _t_dispatch

            # On success: advance cursor
            await self._bus.ack_processed(
                agent_id=agent_id,
                channel_id=channel_id,
                up_to_timestamp=trigger_message.created_at,
            )
            await self._ack_room_seen(
                agent_id, channel_id, trigger_message, is_team, rendered_from
            )

            logger.info(
                f"MessageBusTrigger: agent {agent_id} processed "
                f"{len(messages)} messages in channel {channel_id}"
            )

            if response_text:
                if is_team:
                    # Post the reply back into the shared room as this agent.
                    # Parse @mentions so an agent can hand off to a teammate
                    # (e.g. "@rabbit can you summarise?") and pull them in.
                    mentions = self._extract_team_mentions(response_text, member_map)
                    # Cap agent↔agent cascades: if too many agent hops have
                    # piled up since the last human message, stop propagating
                    # @mentions so two agents can't loop forever.
                    if mentions:
                        depth = await self._team_cascade_depth(channel_id)
                        if depth >= MAX_TEAM_AGENT_HOPS:
                            logger.info(
                                f"Team cascade depth {depth} >= {MAX_TEAM_AGENT_HOPS} "
                                f"in {channel_id}; dropping @mentions to break the loop"
                            )
                            mentions = []
                    await self._bus.send_message(
                        from_agent=agent_id,
                        to_channel=channel_id,
                        content=response_text,
                        mentions=mentions or None,
                        # Stamp the reply with the turn that produced it, so
                        # the transcript can open this turn's full event_log.
                        event_id=turn_event_id,
                    )
                else:
                    # Write response to inbox
                    await self._write_to_inbox(
                        agent_id, channel_id, trigger_message, response_text
                    )

            _hop_done = True

        except CancelledByUser as e:
            # `_hop_done` deliberately stays False: a stopped turn is not a
            # completed hop, and letting it into the [bus-timing] series would
            # mix "how long delivery takes" with "when the owner pressed stop".
            # A stop is a user decision, not a fault. Three things must NOT
            # happen here, each of which the generic handler below would do:
            #
            #  1. `record_failure` — three "failures" poison the message and
            #     `get_pending_messages` filters it out FOREVER. Stopping a
            #     run three times would make that message undeliverable.
            #  2. the owner-facing permanent-failure notice — telling someone
            #     their agent broke when they pressed stop themselves.
            #  3. failure alerting / retry accounting downstream.
            #
            # And one thing that must happen and has no other home: ADVANCE
            # THE CURSOR. On the success path the ack sits at the END of the
            # try block, so an exception skips it — the message would still be
            # pending, the next poll would start the very run the owner just
            # stopped, and stop would read as "it restarted itself".
            logger.info(
                f"MessageBusTrigger: run stopped by owner — agent {agent_id}, "
                f"channel {channel_id} ({e.reason or 'no reason given'})"
            )
            with contextlib.suppress(Exception):
                await self._bus.ack_processed(
                    agent_id=agent_id,
                    channel_id=channel_id,
                    up_to_timestamp=trigger_message.created_at,
                )
                # Stopping does not un-show what was already shown: the prompt
                # was built and the room's scrollback rendered before the model
                # ever got a chance to be interrupted.
                await self._ack_room_seen(
                    agent_id, channel_id, trigger_message, is_team, rendered_from
                )
        except Exception as e:
            logger.exception(
                f"MessageBusTrigger: failed to process channel {channel_id} "
                f"for agent {agent_id}: {e}"
            )
            # Record failure for the trigger message
            await self._bus.record_failure(
                message_id=trigger_message.message_id,
                agent_id=agent_id,
                error=str(e),
            )
            # Once this message crosses the poison threshold,
            # `get_pending_messages` will filter it out forever (local_bus.py)
            # — this is the one chance to tell the owner it happened.
            failure_count = await self._bus.get_failure_count(
                trigger_message.message_id, agent_id
            )
            if failure_count >= POISON_FAILURE_THRESHOLD:
                await self._notify_permanent_failure(
                    agent_id=agent_id,
                    channel_id=channel_id,
                    trigger_message=trigger_message,
                    error=str(e),
                )

        # One line per successful hop, grep-stable — emitted OUTSIDE the try
        # so observation code can never turn an already-delivered-and-acked
        # message into a recorded failure. hop mirrors queue_wait's -1.0
        # convention when created_at was unparseable, so aggregations can
        # drop incomplete rows on a single filter. The fourth quantity is
        # implicit: hop_s - queue_wait_s - turn_s = delivery (the ack +
        # room post / inbox write after the runtime returned) — a real
        # number, not rounding error.
        if _hop_done:
            _hop_s = (
                (time.monotonic() - _t_dispatch) + _queue_wait_s
                if _queue_wait_s >= 0.0 else -1.0
            )
            logger.info(
                "[bus-timing] agent={} channel={} team={} batch={} "
                "queue_wait_s={:.2f} oldest_wait_s={:.2f} turn_s={:.2f} "
                "hop_s={:.2f}".format(
                    agent_id, channel_id, is_team, len(messages),
                    _queue_wait_s, _oldest_wait_s, _turn_s, _hop_s,
                )
            )

    @staticmethod
    def _classify_error(error: str) -> str:
        """Coarse category used for (a) the cooldown de-dup key and (b) the
        hint text shown to the owner. Deliberately a substring match, not a
        parsed exception type — `record_failure` only ever gets a `str(e)`,
        the original exception is already gone by the time this runs.

        Runs on the RAW error (before `_redact_error_for_owner` masks
        anything) — classification only reads for keyword markers like
        "api_key" / "401", it never displays the raw string, so there is
        nothing to redact here.
        """
        return "provider_credential" if is_credential_error(error) else "generic"

    @staticmethod
    def _redact_error_for_owner(error: str) -> str:
        """Mask secret-looking substrings and cap the length before an
        error string is echoed into the owner-facing inbox notification.

        Provider SDKs routinely echo the offending credential back in the
        error body (OpenAI: "Incorrect API key provided: sk-..."), so
        `str(exception)` must never be written verbatim to a place the
        owner (and anyone with inbox access) can read. This is a coarse
        pattern mask, not a full secret scanner — good enough for the
        common `sk-...` / `key=...` / `Bearer ...` shapes, not a security
        boundary for arbitrary provider error formats.
        """
        return redact_secrets(error, MAX_NOTIFIED_ERROR_LEN)

    async def _notify_permanent_failure(
        self,
        agent_id: str,
        channel_id: str,
        trigger_message: BusMessage,
        error: str,
    ) -> None:
        """Surface a permanently-dropped bus message to the owner's inbox.

        Without this, hitting `POISON_FAILURE_THRESHOLD` is a pure silent
        failure (upstream: NetMindAI-Open/NarraNexus#52) — e.g. a broken
        OpenAI provider key makes every `_invoke_runtime` call raise, and
        after 3 failures the message just vanishes from
        `get_pending_messages` forever with zero owner-facing signal.

        De-duplicated per (agent_id, error category) via
        `_failure_notify_cooldown` (same in-memory, per-process pattern as
        `_rate_counters` — a process restart resets it, which is an accepted
        tradeoff here too) so a burst of messages failing for one root cause
        writes at most one inbox row per `FAILURE_NOTIFY_COOLDOWN_SECONDS`.

        The cooldown is armed ONLY after a successful inbox write (see the
        end of the `try` block) — arming it up-front would let one transient
        write failure (DB blip, etc.) silently suppress the real
        notification for the rest of the cooldown window.
        """
        category = self._classify_error(error)
        cooldown_key = f"{agent_id}:{category}"
        now = time.monotonic()
        last_notified = self._failure_notify_cooldown.get(cooldown_key)
        if (
            last_notified is not None
            and now - last_notified < FAILURE_NOTIFY_COOLDOWN_SECONDS
        ):
            return

        try:
            owner_user_id = await self._get_agent_owner(agent_id)
            if not owner_user_id:
                logger.warning(
                    f"Cannot notify of permanent bus failure: agent "
                    f"{agent_id} has no resolvable owner"
                )
                return

            import uuid

            from xyz_agent_context.repository.inbox_repository import (
                InboxRepository,
            )
            from xyz_agent_context.schema.inbox_schema import (
                InboxMessageType,
                MessageSource,
            )
            from xyz_agent_context.utils.db.db_factory import get_db_client

            if category == "provider_credential":
                hint = (
                    "This looks like a provider/credential problem — check "
                    "the agent's LLM provider configuration (API key, base "
                    "URL) in Provider settings, then retry the message."
                )
            else:
                hint = (
                    "Check the agent's recent activity for details, then "
                    "retry the message."
                )

            safe_error = self._redact_error_for_owner(error)
            content = (
                f"Your agent could not process a message on channel "
                f"{channel_id} after {POISON_FAILURE_THRESHOLD} attempts "
                f"and has stopped retrying it automatically.\n\n"
                f"Error: {safe_error}\n\n{hint}"
            )

            db = await get_db_client()
            await InboxRepository(db).create_message(
                user_id=owner_user_id,
                message_id=f"busfail_{uuid.uuid4().hex[:16]}",
                title=f"Message delivery failed: {agent_id}",
                content=content,
                message_type=InboxMessageType.SYSTEM_NOTICE,
                source=MessageSource(type="message_bus_failure", id=channel_id),
            )
            # Arm the cooldown only now that the write actually succeeded.
            self._failure_notify_cooldown[cooldown_key] = now
            logger.warning(
                f"MessageBusTrigger: notified owner {owner_user_id} of "
                f"permanent failure for agent {agent_id} in channel "
                f"{channel_id} (category={category})"
            )
        except Exception as notify_err:  # noqa: BLE001 — notification is best-effort
            logger.warning(
                f"Failed to write permanent-failure notification to inbox: "
                f"{notify_err}"
            )

    async def _team_member_names(self, channel_id: str) -> Dict[str, str]:
        """Map each channel member's agent_id → display name (agent_name)."""
        out: Dict[str, str] = {}
        for m in await self._bus.get_channel_members(channel_id):
            row = await self._bus._db.get_one("agents", {"agent_id": m.agent_id})
            if row:
                out[m.agent_id] = row.get("agent_name") or m.agent_id
        return out

    async def _load_bulletin(self, team_id: str) -> List[Any]:
        """The team's standing rules, or [] if they cannot be read.

        Its own method so the degradation is testable rather than buried in the
        dispatch path. A read failure must NOT take the turn down: the turn is
        still perfectly answerable without the bulletin, so losing it is a
        degradation while losing the reply is an outage. The warning is what
        makes the degradation visible — silently returning [] would present an
        unreachable database as "this team has no rules".
        """
        try:
            from xyz_agent_context.utils.db.db_factory import get_db_client
            from xyz_agent_context.repository import TeamBulletinRepository

            return await TeamBulletinRepository(await get_db_client()).list_for_team(team_id)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[team-bulletin] could not load bulletin for {team_id}, "
                f"continuing without it: {e}"
            )
            return []

    @staticmethod
    def _render_bulletin(bulletin: Optional[List[Any]], member_map: Dict[str, str]) -> List[str]:
        """Render the team bulletin block, or nothing at all.

        Returns [] for an empty/absent bulletin — a stated acceptance criterion,
        not an optimisation. A header with "(none)" under it would ride along in
        every turn of every team that never touches the feature.

        Three groups, in this order:

        1. long-term rules, 2. current-task rules, 3. the auto-summary.

        Rules before summary because the summary is machine guesswork sitting
        next to instructions a human typed; rendered as another numbered rule it
        would be *obeyed* rather than read. It is labelled as automatic and
        possibly stale for the same reason — an agent needs some way to weigh
        the two apart, and there is none in the text itself.

        Agent-written entries are attributed, user-written ones are not: the
        attribution exists so the reader can tell which rules the team invented
        for itself. Everything unattributed came from the person in charge.
        """
        if not bulletin:
            return []

        from xyz_agent_context.schema.team_schema import (
            BULLETIN_SOURCE_AGENT,
            BULLETIN_SOURCE_SUMMARY,
            BULLETIN_TIER_CURRENT_TASK,
        )

        rules = [e for e in bulletin if e.source != BULLETIN_SOURCE_SUMMARY]
        summary = next(
            (e for e in bulletin if e.source == BULLETIN_SOURCE_SUMMARY), None
        )
        if not rules and summary is None:
            return []

        def _label(entry) -> str:
            if entry.source != BULLETIN_SOURCE_AGENT or not entry.author_id:
                return ""
            return f"  (added by {member_map.get(entry.author_id, entry.author_id)})"

        out: List[str] = []
        if rules:
            out += [
                "",
                "[Team Bulletin] — standing rules for this team. They apply to "
                "every reply you make here, including ones the conversation "
                "below says nothing about. Follow them without being reminded.",
            ]
            n = 0
            for e in [r for r in rules if r.tier != BULLETIN_TIER_CURRENT_TASK]:
                n += 1
                out.append(f"{n}. {e.content}{_label(e)}")
            current = [r for r in rules if r.tier == BULLETIN_TIER_CURRENT_TASK]
            if current:
                out.append("For the CURRENT TASK only:")
                for e in current:
                    n += 1
                    out.append(f"{n}. {e.content}{_label(e)}")

        if summary is not None and (summary.content or "").strip():
            out += [
                "",
                "[Team progress] — auto-summarised by the platform, NOT written "
                "by anyone, and it may lag behind what just happened. Treat it "
                "as background, and trust the conversation below over it where "
                "they disagree.",
                summary.content,
            ]
        return out

    async def _run_patrol(self, team_id: str, lead_agent_id: str, channel_id: str) -> None:
        """One patrol sweep: look at the board, chase what is stuck, or say nothing.

        This is the periodic activation the team room otherwise has no way to
        produce. Every member — the lead included — only wakes when @mentioned,
        so a flow advances only while each hop remembers to pass the baton, and
        a hop that forgets kills the chain with nobody structurally able to
        notice. The sweep is what notices.

        Silence is the NORMAL outcome. A patrol that announced "all good" every
        ten minutes would be exactly the standing noise this room's design keeps
        removing (the folded console, the lingering activity bubble). It speaks
        only when the model produced something to say, and even then only within
        the DB-backed speech cap.

        The cursor moves however this ends, crash included: a failed patrol
        still consumed its slot, and retrying it at once turns one broken team
        into a hot loop.
        """
        from xyz_agent_context.message_bus.patrol import mark_patrolled
        from xyz_agent_context.utils.db.db_factory import get_db_client

        db = await get_db_client()
        try:
            # The stack owns the sweep's activity row and its cancellation
            # registration. Both are entered inside `_patrol_body`, at the
            # point a turn is actually going to run — see the note there for
            # why opening the row earlier costs the roster the very link it
            # exists to provide.
            async with contextlib.AsyncExitStack() as stack:
                await self._patrol_body(db, stack, team_id, lead_agent_id, channel_id)
        except Exception as e:  # noqa: BLE001 — a bad sweep never breaks the poller
            logger.warning(f"[patrol] sweep failed for {team_id}: {e}")
        finally:
            await mark_patrolled(db, team_id)

    async def _patrol_body(
        self,
        db: Any,
        stack: contextlib.AsyncExitStack,
        team_id: str,
        lead_agent_id: str,
        channel_id: str,
    ) -> None:
        """The sweep proper, inside the caller's activity/cancellation scope."""
        from xyz_agent_context.agent_runtime.cancel_watcher import get_cancel_watcher
        from xyz_agent_context.agent_runtime.cancellation import CancellationToken
        from xyz_agent_context.message_bus import _bus_activity
        from xyz_agent_context.message_bus.patrol import (
            detect_stalled_items,
            may_patrol_speak,
            note_patrol_spoke,
        )

        # Facts first, and the platform's own: "is this stalled" is derived
        # from activity data, never from the model's read of the room
        # (iron rule #15). The lead's judgement starts after this line.
        #
        # This runs BEFORE the speech cap on purpose. Detection is not part
        # of speaking — it writes `stalled` through to the board, which is
        # what the user's panel renders and what paces the next sweep. When
        # the cap gated it too, a capped team stopped updating its board
        # entirely: items that went quiet during the capped window still
        # read as `in_progress` afterwards, so the UI under-reported and
        # the adaptive interval stayed at the slow pace precisely when
        # things were going wrong. A read plus a status write is also
        # nothing next to the LLM turn the cap actually exists to save.
        stalled = await detect_stalled_items(
            db, team_id, executor_agent_id=lead_agent_id
        )

        # The speech cap is checked BEFORE the turn, not after it.
        #
        # Checking only at post time meant a capped patrol still ran a full
        # LLM turn and threw the output away: with a stalled board the pace
        # is 180s (10 sweeps per 30-minute window) against a cap of 6, so
        # roughly four entire runs per window were burned for nothing —
        # right next to the "empty board, zero runs" cost guarantee this
        # feature advertises. The sweep still records its cursor below, so
        # a capped team does not turn into a hot candidate.
        if not await may_patrol_speak(db, team_id):
            logger.info(
                f"[patrol] speech cap reached for {team_id}; skipping the sweep"
            )
            return

        member_map = await self._team_member_names(channel_id)
        team_owner = await self._get_agent_owner(lead_agent_id)
        history = await self._bus.get_recent_messages(
            channel_id, limit=TEAM_HISTORY_LIMIT
        )
        lead, work_items = await self._team_board(team_id)
        # The patrol sweep is a real turn whose reply lands in the room with
        # @mentions, so it is bound by the team's rules exactly as an @mentioned
        # member is. Omitting this made the Leader the one member the bulletin
        # did not reach — while the tool blurb below still told it the bulletin
        # loads every turn.
        bulletin = await self._load_bulletin(team_id)
        prompt = self._build_team_prompt(
            lead_agent_id, history, member_map,
            owner_user_id=team_owner, team_id=team_id,
            trigger_messages=[],
            bulletin=bulletin,
            lead_agent_id=lead or lead_agent_id,
            work_items=work_items,
            patrol_stalled=[
                {
                    "title": i.title,
                    "assignee": member_map.get(i.assignee_id or "", i.assignee_id or ""),
                    "item_id": i.item_id,
                }
                for i in stalled
            ],
        )

        # A sweep is an ordinary run: it burns tokens, it can wedge, and the
        # owner must be able to stop it. So it gets the same two bindings the
        # message lane gives its runs, for the same reasons.
        #
        # `on_event_id` is the first moment the run HAS an id (Step 0 mints
        # it). Two things hang off that moment:
        #
        #   * the activity row's `event_id`. `start()` writes NULL there, and
        #     the roster's idle branch hands that column to the frontend as the
        #     entry point into the run's event log. Opening the row for the
        #     roster's sake and never filling this in would have cost the
        #     roster the very link it wanted.
        #   * the cancel watcher. The owner's stop lands in the backend
        #     process, not here, so it arrives through the DB and needs a token
        #     registered against the run id in order to fire.
        # The activity row opens HERE — not at the top of the sweep.
        #
        # `start()` writes `event_id: None` and resets `steps` / `started_at`,
        # and nothing writes the id back until `on_event_id` fires below. Open
        # it any earlier and every sweep that returns before running a turn —
        # the speech cap above, or a throw while assembling the prompt — blanks
        # the lead's link to its LAST REAL run's event log on the way past.
        # That is not a corner: with a stalled board the pace is 180s against a
        # cap of 6 per 30 minutes, so the tail of every window is exactly those
        # no-op sweeps.
        #
        # Opening it late costs nothing that was actually being bought. The
        # roster misses the two DB reads above, and `detect_stalled_items` no
        # longer consults this row for the sweeper at all (it takes
        # `executor_agent_id` and skips it), so whether the row is open during
        # detection cannot change a verdict. The board tools' fallback room
        # resolver is only read from inside the runtime call.
        act = await stack.enter_async_context(
            _bus_activity.turn(db, lead_agent_id, channel_id)
        )
        cancellation = CancellationToken()
        watcher = get_cancel_watcher(db)
        watched_run_id: list[str] = [""]
        # Unregister however the sweep exits — a token left behind keeps the
        # poll loop alive for a run that is already gone.
        stack.callback(lambda: watcher.unregister(watched_run_id[0]))

        async def on_event_id(run_id: str) -> None:
            watched_run_id[0] = run_id
            watcher.register(run_id, cancellation)
            await act.note_event_id(run_id)

        response_text, _ = await self._invoke_runtime(
            agent_id=lead_agent_id,
            sender_agent_id=f"{TEAM_ROOM_OWNER_PREFIX}{team_id}",
            prompt=prompt,
            channel_id=channel_id,
            retrieval_anchor="team patrol",
            include_monologue=True,
            team_room=True,
            on_progress=act.on_progress,
            on_event_id=on_event_id,
            cancellation=cancellation,
            # The turn's team, for the MCP identity headers. Without it the
            # board tools this very prompt asks the lead to call cannot prove
            # which room they are in — tools must learn that from the server,
            # never from a model parameter.
            team_id=team_id,
        )
        text = (response_text or "").strip()
        if not text:
            return  # nothing wrong, nothing said
        # Re-checked because the pre-turn gate opened minutes ago and a
        # concurrent sweep in another process may have used the budget.
        if not await may_patrol_speak(db, team_id):
            logger.info(f"[patrol] speech cap reached for {team_id}; staying quiet")
            return
        # Posted under the ROOM's marker, not the lead's id: that is what
        # keeps the line out of the agent-hop count, and it reads honestly
        # — this is the platform taking stock, not the lead chatting.
        await self._bus.send_message(
            from_agent=f"{TEAM_ROOM_OWNER_PREFIX}{team_id}",
            to_channel=channel_id,
            content=text,
            mentions=self._extract_team_mentions(text, member_map) or None,
            msg_type=PATROL_MSG_TYPE,
        )
        await note_patrol_spoke(db, team_id)

    async def _team_board(self, team_id: str) -> tuple[str, List[dict]]:
        """``(lead_agent_id, unfinished work items)`` for a team room's prompt.

        Best-effort: a board that cannot be read degrades to "no items" rather
        than failing the turn. The room conversation is the primary surface —
        losing the board section costs the lead some context, losing the turn
        costs the user their answer.
        """
        if not team_id:
            return ("", [])
        try:
            from xyz_agent_context.repository.team_work_repository import (
                TeamWorkItemRepository,
            )
            from xyz_agent_context.utils.db.db_factory import get_db_client

            db = await get_db_client()
            team = await db.get_one("teams", {"team_id": team_id})
            lead = str((team or {}).get("lead_agent_id") or "")
            items = await TeamWorkItemRepository(db).list_active(team_id)
            return (
                lead,
                [
                    {
                        "item_id": i.item_id,
                        "title": i.title,
                        "assignee_id": i.assignee_id or "",
                        "status": i.status,
                    }
                    for i in items
                ],
            )
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[work-board] could not load board for {team_id}: {e}")
            return ("", [])

    def _build_team_prompt(
        self,
        agent_id: str,
        history: List[BusMessage],
        member_map: Dict[str, str],
        owner_user_id: Optional[str] = "",
        team_id: str = "",
        trigger_messages: Optional[List[BusMessage]] = None,
        lead_agent_id: str = "",
        work_items: Optional[List[dict]] = None,
        patrol_stalled: Optional[List[dict]] = None,
        *,
        bulletin: Optional[List[Any]],
    ) -> str:
        """Group-chat prompt for a team room. The agent's plain reply is posted
        back into the shared room (the user + teammates see it), so — unlike the
        peer/owner-relay path — there is no send_message_to_user_directly step.

        ``history`` is the recent room scrollback (oldest→newest) so the agent
        sees files/images posted by ANYONE, not only the message that @mentioned
        it; ``trigger_messages`` are the @mentions for this agent — what it
        should respond to."""
        from xyz_agent_context.message_bus._bus_attachment_impl import build_bus_markers

        me = member_map.get(agent_id, agent_id)
        teammates = [n for a, n in member_map.items() if a != agent_id]
        roster = ", ".join(teammates) if teammates else "(no other agents yet)"
        lines = [
            "[Team Group Chat]",
            f'You are "{me}" in a team group chat with the user and your '
            f"teammates.",
            f"Channel members RIGHT NOW (besides the user): {roster}.",
            "These are the ONLY participants who can see this chat. Someone "
            "named in the history but not in that list has LEFT or was never "
            "here — they are not present.",
            # Kills the "I forwarded it ✅" white lie: everyone already sees room
            # files, so there is nothing to forward — @mention is enough.
            "Every member already sees every message and file posted in THIS "
            "room (they are in the conversation below). So NEVER 'forward' or "
            "'send' a file that's already here, and never claim you did — to "
            "bring a teammate in, just @mention them and they'll see it too.",
        ]
        if owner_user_id and team_id:
            from xyz_agent_context.utils.workspace_paths import team_shared_dir
            shared = team_shared_dir(owner_user_id, team_id)
            lines.append(
                f"Team shared folder: {shared} — files placed here (via "
                f"bus_share_to_team) are visible to every teammate; open them "
                f"with the Read tool. To find out what is already in there, "
                f"call bus_list_team_files(team_id=\"{team_id}\") rather than "
                f"guessing a path or asking someone to repeat one. "
                f"WRITE ANYTHING THE TEAM SHOULD SEE HERE TOO — reports, "
                f"pages, data files you intend to register as artifacts. Your "
                f"own workspace is private to you, so work left there is work "
                f"your teammates cannot open or continue."
            )

        # Two standing blocks now precede the conversation, in this order:
        # the bulletin (rules that govern HOW you reply), then the work
        # board (WHAT is outstanding) and the Leader's duties. Rules first
        # because they frame everything after them, including the board.
        # A tool nobody is told about is a tool nobody uses. Kept to one line
        # and worded to discourage over-pinning: the bulletin's budget is small
        # and shared with the user's own rules, so an agent that treats it as a
        # notepad crowds out the very rules it is supposed to obey.
        lines += [
            "",
            "If the team settles on a convention that should govern FUTURE "
            "replies (an output format, where files go), pin it with "
            "bus_pin_team_rule so nobody has to repeat it — every teammate "
            "loads it every turn. Findings, status and conversation belong in "
            "the chat, not the bulletin.",
        ]

        # Before the conversation on purpose: these are the standing constraints
        # the messages are to be read UNDER. Appended after twenty lines of chat
        # they would read as a footnote to the chat instead of a frame for it.
        lines += self._render_bulletin(bulletin, member_map)
        # --- The work board, and (for the lead) what it obliges -------------
        #
        # Injected rather than fetched: if seeing your own board required
        # calling a tool, "what did I hand out" would depend on the model
        # choosing to look — the same dependency iron rule #15 keeps off
        # correctness-critical paths. Every member sees the board (it is how
        # they know what they own); only the lead is given the duty to drive it.
        lines += ["", "[Work board] — tasks that outlive this turn:"]
        if work_items:
            for item in work_items:
                who = member_map.get(item.get("assignee_id") or "", "") or "unclaimed"
                lines.append(
                    f"- [{item.get('status')}] {item.get('title')} "
                    f"({who}) · id={item.get('item_id')}"
                )
        else:
            lines.append("- (no open work items)")

        if patrol_stalled is not None:
            # A patrol sweep, not a reply to anyone. Nobody addressed this
            # agent — the platform woke it because work is outstanding.
            lines += [
                "",
                "[Patrol] Nobody messaged you. The platform woke you because "
                "this team has unfinished work and you are its Leader.",
            ]
            if patrol_stalled:
                lines.append(
                    "These items are STALLED — their owner is idle or has gone "
                    "silent. This is measured from real activity data, not a "
                    "guess, so treat it as fact:"
                )
                for s in patrol_stalled:
                    lines.append(f"- {s.get('title')} ({s.get('assignee')})")
                lines += [
                    "Chase them: @mention the owner and ask where it stands. "
                    "DO NOT reassign the work to someone else — 'idle with "
                    "unfinished work' is not 'never coming back', and two "
                    "agents on one deliverable is worse than a late one.",
                    "If something has clearly been delivered but the board "
                    "still says otherwise, close it with work_complete_item.",
                ]
            else:
                lines.append(
                    "Nothing is stalled. If there is genuinely nothing worth "
                    "saying, say NOTHING — reply with empty text. A routine "
                    "'all good' every few minutes is noise in a room the user "
                    "is trying to read."
                )

        if lead_agent_id and lead_agent_id == agent_id:
            lines += [
                "",
                "[You are the Leader of this team]",
                "Nobody else is watching whether this team's work actually "
                "finishes — that is your job, and it does not end when you "
                "hand something out.",
                "- When you assign work, record it: work_add_item(title, "
                "assignee_id). A task that exists only in your reply is a task "
                "nobody can notice has stalled — including you, next time you "
                "wake up, because this turn's memory is gone by then.",
                "- When someone delivers, close it: work_complete_item(item_id).",
                "- The board above is the team's real state. If it disagrees "
                "with what you just read in the room, the room is right and the "
                "board needs updating.",
            ]

        def _sender(msg: BusMessage) -> str:
            return (
                "User"
                if msg.from_agent.startswith(USER_SENDER_PREFIX)
                else member_map.get(msg.from_agent, msg.from_agent)
            )

        lines += ["", "Recent messages (oldest first) — the shared conversation, "
                  "including any files posted by anyone; open a file path with Read "
                  "if you need its contents:"]
        for msg in history:
            # Platform lines are LABELLED, not dropped.
            #
            # Rendered the normal way they read as a member speaking — "Alice:
            # Team bulletin updated." — because a notice's sender is whoever
            # triggered it, and patrol's `team_<id>` marker does not resolve
            # through member_map at all, so it prints raw as a phantom teammate.
            #
            # But dropping them was worse, and briefly shipped: a patrol reply
            # is posted WITH @mentions, so it becomes the mentioned member's
            # trigger message, and the pointer line below prints only the sender
            # — "Respond to that message" — precisely because the text is
            # supposed to be here in the history. Removing it aimed that
            # instruction at nothing. The one sender this filter exists to
            # silence was the only one it broke.
            if (msg.msg_type or "") in PLATFORM_MSG_TYPES:
                lines.append(f"[system] {msg.content}")
                continue
            sender = _sender(msg)
            lines.append(f"{sender}: {msg.content}")
            marker = build_bus_markers(msg.attachments, from_agent=sender)
            if marker:
                lines.append(marker)

        # Point the agent at what it must answer — the latest message that
        # @mentioned it (it's already in the history above, shown in order).
        if trigger_messages:
            tm = trigger_messages[-1]
            # A patrol chase reaches here as a real trigger, and its sender is
            # the synthetic `team_<id>` marker, which member_map cannot resolve.
            # Naming it verbatim invents a teammate the agent may then try to
            # @mention back.
            if (tm.msg_type or "") in PLATFORM_MSG_TYPES:
                who = _platform_trigger_label(tm.msg_type or "")
            else:
                who = _sender(tm)
            lines += [
                "",
                f"You were just @mentioned by {who}. Respond to that "
                f"message. If it refers to a file/image shown above, open the "
                f"path with the Read tool first, then reply.",
            ]
        lines += [
            "",
            "Write your chat reply now. Rules:",
            "- Output ONLY the message itself — natural, conversational text "
            "(markdown is fine). It is posted to the group as-is; everyone sees it.",
            # Distinguish REPLY-DELIVERY functions (forbidden — the reply
            # auto-posts, so re-sending double-delivers) from ACTION tools
            # (allowed): Read views a file; bus_share_to_team publishes a file to
            # the team folder (it stages bytes, it does NOT post a message). A
            # blanket "no tools" ban made agents refuse to open a shared image and
            # even fake a "forwarded ✅" they couldn't actually do.
            "- Do NOT deliver your answer through a function: no "
            "send_message_to_user_directly, no bus_send_message/bus_send_to_agent "
            "to post this reply — your text below is posted to the group "
            "automatically. You MAY use action tools that DO something: the "
            "built-in Read tool to open a file path shown above, and "
            "bus_share_to_team to publish a file YOU produced to the team folder "
            "(then mention the returned path in your reply). Do the action, then "
            "reply with plain text.",
            "- Do NOT narrate your process or thinking. No \"Let me…\", no \"I "
            "need to find…\", no tool/function names, no step-by-step. Just talk.",
            "- Keep it short, like a real group chat. To pull in a teammate, "
            "@mention them by name (e.g. @Name); say @all for everyone — but only "
            "when you genuinely need them, not as a reflex.",
            "- You may ONLY @mention a current channel member listed above. Do "
            "NOT @mention anyone who is not in that list — they are not in the "
            "channel and cannot see or answer it. If you want someone else "
            "involved, ask the user to add them instead of @mentioning them.",
        ]
        return "\n".join(lines)

    def _extract_team_mentions(
        self, text: str, member_map: Dict[str, str]
    ) -> List[str]:
        """Resolve @mentions in an agent's reply to channel-member agent_ids
        (or ["@everyone"] for @all/@everyone), so a hand-off pulls teammates in."""
        tokens = {t.lower() for t in re.findall(r"@([\w一-鿿]+)", text or "")}
        if not tokens:
            return []
        if "all" in tokens or "everyone" in tokens:
            return ["@everyone"]
        out: List[str] = []
        for aid, name in member_map.items():
            nm = (name or aid).lower()
            first = nm.split()[0] if nm.split() else nm
            if nm in tokens or first in tokens or any(
                len(t) >= 2 and nm.startswith(t) for t in tokens
            ):
                out.append(aid)
        return out

    async def _team_cascade_depth(self, channel_id: str) -> int:
        """How many consecutive agent (non-user) messages end the channel — i.e.
        how many agent hops have happened since the last human message. A user
        message resets this to 0 on its next turn."""
        ph = self._bus._db.placeholder
        # Platform lines are excluded IN SQL, not skipped after the fact.
        #
        # A patrol line is the PLATFORM taking stock, not an agent taking a
        # turn, so it must not count toward the cap (owner decision
        # 2026-08-07, option a) — otherwise the exemption is self-defeating:
        # patrol speaks into a room that is already at the cap precisely
        # because the flow broke, and its own line would push every later
        # chase @ out of reach.
        #
        # The same sentence is true word for word of the stop notice and the
        # bulletin notice, which were left in when this was written because
        # patrol was the only one that existed. They are now all excluded
        # through one shared tuple, so the next platform message type cannot
        # be forgotten here (see `system_messages`).
        #
        # Filtering in Python was not enough. The window is a fixed LIMIT, so
        # a skipped row still consumed a slot in it: with 3 patrol lines among
        # the last 6 messages, only 3 countable hops fit, `depth` could never
        # reach MAX_TEAM_AGENT_HOPS, and the runaway-@ cap silently stopped
        # applying — in exactly the rooms patrol frequents, since it only
        # speaks where a chain is already looping.
        rows = await self._bus._db.execute(
            f"SELECT from_agent FROM bus_messages WHERE channel_id = {ph} "
            f"AND (msg_type IS NULL OR msg_type NOT IN ({_platform_placeholders(ph)})) "
            f"ORDER BY created_at DESC LIMIT {MAX_TEAM_AGENT_HOPS + 2}",
            (channel_id, *PLATFORM_MSG_TYPES),
        )
        depth = 0
        for r in rows or []:
            if str(r["from_agent"]).startswith(USER_SENDER_PREFIX):
                break
            depth += 1
        return depth

    async def _incoming_is_reply_to_my_errand(
        self,
        agent_id: str,
        channel_id: str,
        incoming: List[BusMessage],
    ) -> bool:
        """Is this batch an ANSWER to an errand of ours, or a QUESTION to us?

        Decided from a fact recorded on the message itself
        (``bus_messages.sender_turn_source``): the sender writes WHICH KIND of
        turn produced it — an owner-facing turn ("chat"/"job"/…) or an
        errand-continuation bus turn (``BUS_ERRAND_TURN_SOURCE``) means it is
        running an errand and asking us; plain "message_bus" means it was in
        a peer-ANSWERING turn.

        Plain "message_bus" alone is NOT sufficient for Owner Relay, because
        the stamp records the sender's turn kind, not this message's intent:
        an agent in a peer-answering turn can still ASK (fan out to a third
        agent to compose its answer). So the batch only reads as a reply if
        WE actually hold an errand in this channel — at least one prior send
        of ours stamped with a non-"message_bus" source (or a pre-stamp
        legacy NULL, which gets the old benefit of the doubt). An agent that
        never asked anything here cannot be owed an answer.

        The errand stamp is per-SEND, not per-turn, and that is load-bearing
        here: a bus turn also answers unrelated peers whose unread the platform
        injected from other channels, so only sends aimed at the sender's own
        errand scope carry it (``_send_turn_source`` in the bus module's MCP
        tools). Stamping the whole turn broke exactly this method's contract
        from the other side — an ANSWER to an unrelated peer arrived stamped as
        a question, so that peer stopped relaying to its own owner
        (2026-08-03 review).

        Residual holes, accepted and documented — do not read this list as
        "risk exhausted", read it as what is known:

        1. Stale errand: if we once ran an errand in this channel and a peer
           LATER asks us a fresh question from a peer-ANSWERING turn (a case
           no prompt of ours guides), the old errand rows still vote Owner
           Relay. Per-message intent would need the sender to declare
           ask-vs-answer per send; a turn-kind stamp cannot express it. Equals
           the pre-fix behaviour.
        2. Mutual live errands in one DM channel: DM channels are reused
           symmetrically, so if the errand peer ALSO asked us something and we
           answer them inside our errand-continuation turn, that answer is
           aimed at the errand scope and carries the errand stamp — they then
           answer the peer instead of relaying to their own owner. Needs both
           owners to have errands in flight at the same time toward each
           other. This is the direction we chose: the alternative (stamping
           the whole turn) broke the case the platform ITSELF guides — unread
           from other channels is injected every turn and the prompt requires
           answering it — so it fired far more often.
        3. Group errand channel: the errand scope matches by channel as well
           as by peer, so a send into a GROUP channel that is the errand
           channel stamps every member's copy as a question. Bus errands run
           over auto-created DM channels, so this needs a hand-built group
           channel used as an errand channel.
        4. Uppercase / hand-written stamps: the errand-row check compares the
           stored value exactly (SQL ``<>``), so a row written outside our
           writers with a different casing counts as an errand row → Owner
           Relay. Same direction as every other degradation here.

        Closing 1 and 2 for good needs per-message intent — the sender saying
        "this one is a question" on each send — which the review floated and
        which we did not take, because it puts a correctness-critical bit back
        on model obedience (iron rule #15: a machine-knowable fact must not
        depend on which model the user picked). Revisit only with a default
        that is derived, never assumed.

        Why not infer it from channel ordering (the first attempt, twice):
        "have I spoken here" flips as soon as we answer once, so a follow-up
        question re-broke the bug; "who opened the channel" is fixed forever,
        because ``send_to_agent`` finds a DM channel SYMMETRICALLY and REUSES
        it — so once A has DM'd B, every errand B later runs toward A is
        misclassified for BOTH sides, permanently (P1 2026-08-03 review). The
        fact is per-message, so it has to be stored per-message.

        Degradation, in order: unknown source but WE have never spoken here →
        we are plainly the asked party; otherwise → Owner Relay, the
        pre-2026-08-01 behaviour (a wrongly-relayed answer is cosmetic, while
        wrongly suppressing Owner Relay resurrects the silent failure that
        directive exists to prevent).
        """
        sources = {
            (getattr(m, "sender_turn_source", None) or "").strip().lower()
            for m in incoming
        }
        sources.discard("")
        if sources:
            if sources != {"message_bus"}:
                # Any owner-facing or errand-continuation send in the batch
                # means we are being asked.
                return False
            # Every incoming message came from a peer-answering turn. Reply
            # to OUR errand only if we actually have one here.
            return await self._i_have_errand_in_channel(agent_id, channel_id)

        # No recorded source (legacy rows, or an adapter that dropped the
        # header). Fall back to "have we ever spoken here": absence is still
        # unambiguous — a channel we have never sent into cannot hold a reply
        # to an errand of ours.
        try:
            incoming_ids = {
                m.message_id for m in incoming if getattr(m, "message_id", None)
            }
            ph = self._bus._db.placeholder
            rows = await self._bus._db.execute(
                # No human-sender filter here on purpose: from_agent is bound
                # to agent_id, so a usr_-prefixed sender cannot match anyway.
                # (_team_cascade_depth needs one because it reads EVERY message
                # in the channel.) A dead predicate would be worse than none —
                # 'usr_%' is not even precise, since _ is a LIKE wildcard.
                f"SELECT message_id FROM bus_messages WHERE channel_id = {ph} "
                f"AND from_agent = {ph}",
                (channel_id, agent_id),
            )
            for r in rows or []:
                if str(r["message_id"]) not in incoming_ids:
                    return True
            return False
        except Exception as e:  # noqa: BLE001 — prompt shaping, never flow control
            logger.debug(
                f"MessageBusTrigger: could not classify channel {channel_id} "
                f"({e}); assuming owner-relay"
            )
            return True

    async def _i_have_errand_in_channel(
        self, agent_id: str, channel_id: str
    ) -> bool:
        """Did WE ever ask something in this channel on an errand?

        True when at least one of our own sends here was stamped with a
        non-"message_bus" turn source (owner-facing turn, or the
        errand-continuation stamp) — or carries a legacy NULL stamp, which
        predates ``sender_turn_source`` and keeps the pre-fix benefit of the
        doubt. False when we never spoke here, or every send of ours was a
        peer-answering turn: then an incoming "message_bus"-stamped batch
        cannot be a reply owed to us, it is a fresh question.

        DB error → True (Owner Relay), same degradation direction as the
        caller: the silent-failure mode this directive exists to prevent is
        worse than a cosmetic extra relay.
        """
        try:
            ph = self._bus._db.placeholder
            # Existence check, pushed down: this runs on the most common bus
            # trigger path ("a peer answered me"), and DM channels are found
            # symmetrically and REUSED forever — so a client-side scan would
            # grow without bound over the life of an agent pair. IS NULL / <>
            # / LIMIT 1 mean the same thing on SQLite and MySQL.
            rows = await self._bus._db.execute(
                f"SELECT 1 FROM bus_messages "
                f"WHERE channel_id = {ph} AND from_agent = {ph} "
                f"AND (sender_turn_source IS NULL OR sender_turn_source <> {ph}) "
                f"LIMIT 1",
                (channel_id, agent_id, WorkingSource.MESSAGE_BUS.value),
            )
            return bool(rows)
        except Exception as e:  # noqa: BLE001 — prompt shaping, never flow control
            logger.debug(
                f"MessageBusTrigger: errand check failed for channel "
                f"{channel_id} ({e}); assuming owner-relay"
            )
            return True

    def _build_prompt(
        self,
        messages: List[BusMessage],
        owner_user_id: Optional[str] = "",
        owner_name: str = "",
        *,
        i_started_this_exchange: bool,
    ) -> str:
        # NOTE: this builds the full EXECUTION prompt (peer messages + the
        # repeated Owner-Relay boilerplate). For narrative retrieval, embed
        # build_bus_anchor(messages) instead — see the 2026-06-01 design doc.
        """
        Build a prompt from a list of pending messages.

        Includes all messages in the batch so the agent has full context.

        If `owner_user_id` is known, appends an owner-relay directive telling
        the agent it MUST call send_message_to_user_directly(user_id=<owner>,
        ...) to surface the peer exchange back into the owner's chat. Without
        this directive, agents treat peer exchanges as self-contained (they
        reply to the peer or stay silent), and the original owner who asked
        "go talk to agent B for me" never hears back — the reply only lands
        in the Inbox. observed as a silent-failure UX issue in production.

        ``i_started_this_exchange`` decides WHICH directive applies, and it
        matters (P1 2026-08-03, verified live 3/3): Owner Relay was appended
        on every DM turn, so an agent receiving a FRESH question from a peer
        was told "your owner originally asked you to contact this peer agent,
        they are waiting in chat" — false, and it made the recipient answer
        the OWNER and treat the errand as discharged. The asking agent
        (which had promised its user a report) was left waiting forever.
        The models were obeying us; the prompt was lying to them.

        - True  → this batch is the REPLY to an errand of ours: relay it to
                  our owner.
        - False → we are the one being ASKED: answer the PEER on the bus.
                  Our owner asked for nothing and is not waiting.

        Decided by ``_incoming_is_reply_to_my_errand`` from a fact recorded
        on the message itself (``sender_turn_source``) — NOT from "have I
        spoken in this channel", which is only the degradation path and is
        wrong for follow-ups (see that method for why, and for the residual
        holes this pair of directives still has).

        Note the directive an OWNER-RELAY turn receives (item 3: "send a
        clarifying question with bus_send_to_agent") produces a bus send from
        a bus turn, which is why the same verdict also travels to the tools as
        this turn's errand scope — see ``_invoke_runtime``.
        """
        from xyz_agent_context.message_bus._bus_attachment_impl import build_bus_markers

        lines = ["[Message Bus - Incoming Messages]", ""]
        for msg in messages:
            block = (
                f"From: {msg.from_agent}\n"
                f"Time: {msg.created_at}\n"
                f"{msg.content}\n"
            )
            marker = build_bus_markers(msg.attachments, from_agent=msg.from_agent)
            if marker:
                block += f"{marker}\n"
            lines.append(block)

        if owner_user_id and not i_started_this_exchange:
            # Inbound question: the peer is waiting, not our owner.
            lines.append("")
            lines.append("---")
            lines.append("")
            lines.append("## Answer the peer — REQUIRED")
            lines.append("")
            lines.append(
                "A peer agent is asking YOU something. You did not start this "
                "exchange and your owner has not asked you for anything, so "
                "your owner is NOT waiting in chat for this."
            )
            lines.append("")
            lines.append(
                "**The peer cannot see anything you tell your owner.** The only "
                "channel that reaches the agent who asked is a bus reply. If the "
                "peer is relaying a question on ITS owner's behalf, that human is "
                "waiting at the other end of the peer — answering your own owner "
                "instead leaves them waiting forever."
            )
            lines.append("")
            lines.append("**What to do this turn:**")
            lines.append(
                "1. If you can answer → reply to the asker with "
                "`bus_send_to_agent(to_agent_id=<the sender above>, "
                "content=<your answer>)`. This is the point of the turn."
            )
            lines.append(
                "2. If you need something clarified before you can answer → ask "
                "the peer back via `bus_send_to_agent`."
            )
            lines.append(
                "3. Only ALSO call `send_message_to_user_directly` when your "
                "owner genuinely needs to know (a decision only they can make, "
                "or something affecting their work). It is never a substitute "
                "for replying to the peer."
            )
            lines.append(
                "4. Silence is only correct for a closing acknowledgment "
                "(\"thanks\", \"got it\"). A question is never a ping-pong — "
                "answer it."
            )
        elif owner_user_id:
            lines.append("")
            lines.append("---")
            lines.append("")
            lines.append("## Owner Relay — REQUIRED")
            lines.append("")
            lines.append(
                f"Your owner **{owner_name or owner_user_id}** originally asked "
                f"you to contact this peer agent. They are waiting in chat for "
                f"the answer."
            )
            lines.append("")
            lines.append(
                "The owner's chat view does NOT automatically receive the "
                "peer's reply. The ONLY channel that surfaces this exchange "
                "to the owner is `send_message_to_user_directly`. If you do "
                "not call it, the owner sees nothing — they only know "
                "there's a new entry in some inbox they may not be looking "
                "at. This is a silent-failure pattern we explicitly want to "
                "avoid."
            )
            lines.append("")
            lines.append("**What to do this turn:**")
            lines.append(
                "1. Understand the peer reply above."
            )
            lines.append(
                "2. If the peer's reply answers / progresses the owner's "
                "original request → call "
                f"`send_message_to_user_directly(agent_id=<you>, "
                f"user_id=\"{owner_user_id}\", content=<summary + peer "
                "quote>)`. Make the summary actionable: what did the peer "
                "say, what does it mean for the owner's task, what's next."
            )
            lines.append(
                "3. If the peer needs a clarifying follow-up from you → "
                "send it via `bus_send_to_agent`, THEN also call "
                "`send_message_to_user_directly` with a short status "
                "update (\"asked peer for X, waiting for clarification\") "
                "so the owner knows the thread is alive."
            )
            lines.append(
                "4. Silence is the wrong default. Only stay silent if the "
                "peer message is truly irrelevant (e.g. a closing "
                "acknowledgment you already reported to the owner)."
            )

        return "\n".join(lines)

    async def _invoke_runtime(
        self,
        agent_id: str,
        sender_agent_id: str,
        prompt: str,
        channel_id: str,
        trigger_message_id: str = "",
        retrieval_anchor: str = "",
        errand_continuation: bool = False,
        on_progress=None,
        on_event_id=None,
        include_monologue: bool = False,
        team_room: bool = False,
        team_id: str = "",
        cancellation=None,
        root_run_id: str = "",
    ) -> tuple[str, Optional[str]]:
        """
        Invoke AgentRuntime.run() for the given agent with the prompt.

        ``errand_continuation`` is the DM classifier's verdict ("this batch
        answers an errand I started"). When true, this turn's ERRAND SCOPE
        (the peer that answered us + the channel it happened in) rides
        ``trigger_extra_data`` into ``ctx_data.extra_data``, so a bus send
        aimed at that peer can stamp itself ``BUS_ERRAND_TURN_SOURCE`` instead
        of the plain "message_bus" — otherwise a clarifying follow-up sent
        from this turn is indistinguishable from an answer and the RECIPIENT's
        classifier routes it to Owner Relay (P1 recurrence, path A of the
        2026-08-03 review).

        The scope, not a whole-turn flag: bus unread is injected from ALL
        channels every turn and the module prompt requires answering it, so
        this same turn routinely also answers an unrelated peer. Stamping the
        turn marked that answer as a question too, and that peer then stopped
        relaying to ITS owner — the same P1, one seat over (same review). Only
        the send site knows its target, so only the send site can decide; see
        ``_message_bus_mcp_tools._send_turn_source``.

        Returns ``(response_text, event_id)`` — the collected agent response
        text plus the turn's events-row id (None if the run died before
        Step 0). The team branch stamps the id onto the reply it posts back
        into the room, so the transcript can open that turn's event_log.

        `on_event_id`, when provided (team branch only), is forwarded to
        `collect_run` so the turn's events-row id gets bound onto the
        activity row for the team UI.

        Raises:
            RuntimeError: If AgentRuntime cannot be imported or execution fails.
        """
        try:
            from xyz_agent_context.agent_runtime.client import (
                get_agent_runtime_client,
            )
        except ImportError as e:
            raise RuntimeError(
                f"Cannot import AgentRuntime dependencies: {e}"
            ) from e

        collection = await get_agent_runtime_client().run_and_collect(
            agent_id=agent_id,
            user_id=sender_agent_id,
            input_content=prompt,
            working_source=WorkingSource.MESSAGE_BUS,
            on_progress=on_progress,
            on_event_id=on_event_id,
            include_monologue=include_monologue,
            # Rides the extra_kwargs seam straight to AgentRuntime.run — no
            # signature change anywhere in between (collect_run's docstring
            # names `cancellation` as a supported pass-through). Until now
            # this was always the runtime's own no-op token, which is why a
            # bus run could not be stopped from anywhere.
            cancellation=cancellation,
            trigger_extra_data={
                "bus_channel_id": channel_id,
                "retrieval_anchor": retrieval_anchor,
                # Delivery-contract marker: team rooms auto-post plain text
                # and their prompt forbids delivery tools, so the collection
                # (context_runtime) empties the turn's WHOLE expressive
                # surface on this marker — every declarer, both frameworks'
                # reminders. MessageBusModule's own gate is a second line
                # of defense on its declaration.
                BUS_TEAM_ROOM_EXTRA_KEY: team_room,
                # Errand scope — empty unless this turn continues our own
                # errand. sender_agent_id is the peer whose reply triggered us,
                # i.e. exactly who a follow-up would go to.
                "bus_errand_peer": sender_agent_id if errand_continuation else "",
                "bus_errand_channel": channel_id if errand_continuation else "",
                # Trigger tree. Two consumers, both needed: the client hands it
                # to RunRecorder (so this run's events row joins the tree) and
                # context_runtime injects it into the MCP identity headers (so a
                # send from this turn stamps the next message and the tree
                # survives the next hop).
                "root_run_id": root_run_id,
                # The team whose room this turn runs in ("" outside a team).
                # The trigger already resolved it above; publishing it here is
                # what lets tools learn the team from the SERVER rather than
                # from a model-filled parameter (see module/_mcp_identity.py).
                # Not folded into bus_errand_channel: that one is stamped only
                # for errand continuations, so it is empty on most team turns.
                "bus_team_id": team_id,
                "trigger_id": (
                    f"bus_{trigger_message_id}"
                    if trigger_message_id
                    else f"bus_chan_{channel_id}"
                ),
            },
        )

        # Error path (Bug 2): previously the loop only checked
        # AGENT_RESPONSE; if the agent run errored (e.g. owner removed
        # their provider, system default exhausted) the sender agent got
        # an empty string and had to guess why. Now we surface the error
        # inline so the sender sees what went wrong.
        if collection.is_error:
            logger.warning(
                f"[MessageBusTrigger] agent {agent_id} run failed in "
                f"channel {channel_id}: {collection.error.error_type}: "
                f"{collection.error.error_message}"
            )
            return (
                f"⚠️ I couldn't process your message right now "
                f"({collection.error.error_type}). {collection.error.error_message}",
                collection.event_id,
            )

        return collection.output_text, collection.event_id

    async def _write_to_inbox(
        self, agent_id: str, channel_id: str,
        trigger_message: BusMessage, agent_response: str,
    ) -> None:
        """Write the agent's response to the recipient user's inbox.

        Uses `InboxRepository.create_message` (the canonical writer) so
        the row shape stays in sync with the `inbox_table` schema —
        previous hand-written `db.insert("inbox_table", ...)` referenced
        `agent_id` / `owner_user_id` / `updated_at` columns that don't
        exist and omitted the required `message_id`, producing
        `Unknown column 'agent_id' in 'field list'` 13 times in 3
        hours on EC2 2026-05-18.
        """
        try:
            import uuid

            from xyz_agent_context.repository.inbox_repository import InboxRepository
            from xyz_agent_context.schema.inbox_schema import (
                InboxMessageType,
                MessageSource,
            )
            from xyz_agent_context.utils.db.db_factory import get_db_client

            db = await get_db_client()
            agent_row = await db.get_one("agents", {"agent_id": agent_id})
            if not agent_row:
                logger.warning(f"Cannot write to inbox: agent {agent_id} not found")
                return
            recipient_user_id = agent_row.get("created_by", "")
            if not recipient_user_id:
                logger.warning(
                    f"Cannot write to inbox: agent {agent_id} has no created_by"
                )
                return

            repo = InboxRepository(db)
            await repo.create_message(
                user_id=recipient_user_id,
                message_id=f"bus_{uuid.uuid4().hex[:16]}",
                title=f"Message Bus: {trigger_message.from_agent}",
                content=agent_response,
                message_type=InboxMessageType.MESSAGE_BUS,
                source=MessageSource(type="message_bus", id=channel_id),
            )
            logger.info(
                f"Wrote MessageBus result to inbox for user {recipient_user_id}"
            )
        except Exception as e:
            logger.warning(f"Failed to write to inbox: {e}")


async def _get_bus() -> LocalMessageBus:
    """Create and return a LocalMessageBus instance from environment config.

    Works with both SQLite (local) and MySQL (cloud) backends — LocalMessageBus
    is a misnomer; it's a database-backed bus that runs against any backend.
    """
    from xyz_agent_context.utils.db.db_factory import get_db_client

    db = await get_db_client()
    backend = db._backend

    # Ensure all tables exist (schema_registry covers all 26 tables including bus)
    from xyz_agent_context.utils.db.schema_registry import auto_migrate
    await auto_migrate(backend)

    # Initialise the system-default quota subsystem so bus-triggered
    # agent turns can fall back to the free-tier config when the owner
    # hasn't configured their own provider.

    return LocalMessageBus(backend=backend)


async def main() -> None:
    """Entry point for standalone execution."""
    logger.info("Starting MessageBusTrigger...")
    bus = await _get_bus()
    trigger = MessageBusTrigger(bus=bus)

    try:
        await trigger.start()
    except KeyboardInterrupt:
        trigger.stop()
        logger.info("MessageBusTrigger stopped by user")


if __name__ == "__main__":
    from xyz_agent_context.utils.logging import setup_logging
    setup_logging("message_bus_trigger")
    try:
        asyncio.run(main())
    finally:
        asyncio.run(logger.complete())
