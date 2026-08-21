"""
@file_name: executor_reaper.py
@author:
@date: 2026-06-17
@description: Idle-cull coordinator for per-user Executor containers.

Pure coordinator (dependency-injected): it owns neither the concurrency
state nor the docker transport. It periodically asks the admission
controller which users have gone idle past the TTL, and asks a ``stop_fn``
(the broker client) to stop them. This keeps the concerns separate:
  - AgentAdmissionController — concurrency + idle bookkeeping
  - ExecutorReaper          — WHEN to cull (this file)
  - run_recorder.first_live_run_id — IS the user actually idle (DB truth)
  - broker_client.stop_executor — HOW to stop (docker transport)

Binding rule #14: only idle executors are ever reaped — a running loop is
never interrupted. The cull just delays the next start by a cold boot,
surfaced to the user via the "waking up" UX.

That guarantee needs the third collaborator, and used to be stated
without it. The admission controller is a PER-PROCESS singleton while
executor containers are shared per USER, so "zero active loops" only ever
meant "zero in the process asking". The cloud orchestrator runs as
backend + workers and this reaper only runs in backend, so every run
driven by workers (group chat / message bus, scheduled jobs, channel
triggers) was invisible to it — and on 2026-07-31 it stopped the
container out from under a live group-chat reply, surfacing to the user
as ``infra_transient``. The idle claim is therefore vetoed by a
cross-process liveness check against the ``events`` table, which every
process writes to (incident lesson #5).
"""
from __future__ import annotations

import asyncio
import os
from typing import Awaitable, Callable, Optional

from loguru import logger

from xyz_agent_context.agent_runtime.admission import (
    AgentAdmissionController,
    BusyCheck,
    get_admission_controller,
)
from xyz_agent_context.agent_runtime.run_recorder import RECORDING_DISABLED_ENV
from xyz_agent_context.schema.executor_audit import (
    EVENT_CULL_DISABLED,
    EVENT_CULL_SKIPPED_BUSY,
)

StopFn = Callable[[str], Awaitable[None]]

DEFAULT_IDLE_TTL_SEC = 1200   # 20 min (locked decision)
DEFAULT_INTERVAL_SEC = 120

# Stand-in run id for "we could not find out". Not a real run, but it gets a
# real run's answer — hands off. Never written to a cull_skipped_busy row:
# that metric counts runs actually saved, and "unknown" in the run_id column
# would make it unreadable. The blind case has its own pass-level reporting
# (see _report_pass).
UNKNOWN_RUN = "unknown"

# Callers already warned that run recording is off. Bounded (two callers),
# and reset when the switch goes back on so a second pull warns again.
_recording_off_warned: set[str] = set()

# Last pass's outcome, for the L2 read-side (see reaper_status). Module level
# because the reaper is one background task per process and the admin route
# has no handle on the instance; a dict, so a process that never started one
# reports "not running" rather than lying with zeros.
_LAST_PASS: Optional[dict] = None

# Passes between repeats of the "culling is blind" warning. The first blind
# pass warns immediately; at the 120s default this then repeats hourly.
# Warning every pass would be noise nobody reads; warning once would vanish
# with the next `docker restart` (incident lesson #5) while the state persists
# in the environment.
_BLIND_WARN_EVERY = 30


def reaper_status() -> dict:
    """L2 health of the idle-cull reaper, for the admin runtime snapshot.

    Answers the question the ``cull_skipped_busy`` metric cannot: culling
    stopping entirely looks exactly like nothing needing to be culled. The
    fail-safe paths (recording kill switch on, DB unreadable) make EVERY user
    read as busy, so a blind reaper reaps zero forever and is otherwise
    indistinguishable from a healthy idle system — until executors pile up to
    the broker's cap and new users start getting refused.

    ``blind_passes`` is the field to alert on: non-zero and climbing means
    nothing is reclaiming executors in this process.
    """
    if _LAST_PASS is None:
        return {"running": False}
    return {"running": True, **_LAST_PASS}


async def live_run_elsewhere(
    user_id: str,
    *,
    exclude_run_id: Optional[str] = None,
    caller: str = "reaper",
) -> Optional[str]:
    """The id of a run live in ANY process for this user, or None.

    THE cross-process "is this user busy?" call for anything that stops or
    destroys a container. Reads the ``events`` table because that is the only
    place every process's runs meet.

    Never raises. Every failure path answers "busy", with ``UNKNOWN_RUN`` as
    the id so callers can tell a real blocker from an unknowable one. Not
    knowing must never authorise destroying anything (binding rule #14).
    """
    from xyz_agent_context.agent_runtime.run_recorder import (
        first_live_run_id,
        recording_enabled,
    )

    if not recording_enabled():
        # The kill switch for trigger-path run recording turns off exactly
        # the runs this guard exists to protect: without a recorder their
        # events row never flips to 'running', so the DB would report them
        # idle. An observability switch must not silently become a licence to
        # destroy containers — while it is on, nothing is reapable.
        if caller not in _recording_off_warned:
            _recording_off_warned.add(caller)
            logger.warning(
                f"[{caller}] run recording is disabled ({RECORDING_DISABLED_ENV}) "
                f"— cross-process liveness is unknowable, so executor "
                f"idle-culling is OFF while it stays disabled."
            )
        return UNKNOWN_RUN
    _recording_off_warned.discard(caller)
    try:
        from xyz_agent_context.utils.db.db_factory import get_db_client

        db = await get_db_client()
        return await first_live_run_id(db, user_id, exclude_run_id=exclude_run_id)
    except Exception as e:  # noqa: BLE001 — no verdict ⇒ assume busy
        logger.warning(f"[{caller}] busy check unavailable for user={user_id}: {e}")
        return UNKNOWN_RUN


class _CullVeto:
    """The reaper's ``is_busy`` veto, with per-RUN audit de-duplication.

    A vetoed user KEEPS its idle stamp (that is the point — see
    ``claim_idle_users``), so it is re-offered and re-vetoed every pass for
    as long as its run lives. Auditing each of those would make the row count
    a function of RUN DURATION: one legitimate 10-hour agent (rule #14 says
    that is normal) would write ~300 rows and read as hundreds of near-misses.
    The metric counts runs saved, so each (user, run) is recorded once.

    Bounded by a hard cap rather than by expiry: entries are dropped when the
    user comes back clean, and a user that goes active in THIS process stops
    being a candidate without ever doing so. Evicting the oldest at the cap
    costs a duplicate audit row in a case that needs thousands of
    simultaneously-blocked users to reach.
    """

    _MAX_TRACKED = 4096

    def __init__(self, check=live_run_elsewhere) -> None:
        self._check = check
        self._blocked_by: dict[str, str] = {}   # user_id -> blocking run id
        # Per-pass tallies. The veto is the only thing that sees the
        # candidates — claim_idle_users returns just the survivors, so
        # "nobody was due" and "everybody was vetoed" are the same empty list
        # from the reaper's side. Counted here, drained by take_pass_stats.
        self._judged = 0
        self._vetoed = 0
        self._blind = 0     # vetoes that were "could not tell", not "busy"

    def take_pass_stats(self) -> dict:
        stats = {
            "judged": self._judged, "vetoed": self._vetoed, "blind": self._blind,
        }
        self._judged = self._vetoed = self._blind = 0
        return stats

    async def __call__(self, user_id: str) -> bool:
        run_id = await self._check(user_id)
        self._judged += 1
        if run_id is None:
            self._blocked_by.pop(user_id, None)
            return False
        self._vetoed += 1
        if run_id == UNKNOWN_RUN:
            self._blind += 1
        if self._blocked_by.get(user_id) != run_id:
            if len(self._blocked_by) >= self._MAX_TRACKED:
                self._blocked_by.pop(next(iter(self._blocked_by)))
            self._blocked_by[user_id] = run_id
            logger.info(
                f"[reaper] skipping user={user_id}: run {run_id} is live in "
                f"another process (idle here, busy elsewhere)"
            )
            if run_id != UNKNOWN_RUN:
                await _audit(
                    EVENT_CULL_SKIPPED_BUSY, user_id=user_id, run_id=run_id,
                )
        return True


async def _audit(
    event_type: str,
    *,
    user_id: Optional[str] = None,
    run_id: Optional[str] = None,
    detail: Optional[dict] = None,
) -> None:
    """Best-effort audit row. Never raises — the observer must not break the
    pass it is observing."""
    try:
        from xyz_agent_context.repository.executor_audit_repository import (
            ExecutorAuditRepository,
        )
        from xyz_agent_context.utils.db.db_factory import get_db_client

        await ExecutorAuditRepository(await get_db_client()).record(
            event_type=event_type, user_id=user_id, run_id=run_id, detail=detail,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[reaper] audit {event_type} failed user={user_id}: {e}")


class ExecutorReaper:
    """Periodically stops executors whose user has been idle past the TTL."""

    def __init__(
        self,
        controller: AgentAdmissionController,
        stop_fn: StopFn,
        *,
        is_busy: Optional[BusyCheck],
        ttl_seconds: float = DEFAULT_IDLE_TTL_SEC,
        interval_seconds: float = DEFAULT_INTERVAL_SEC,
    ) -> None:
        self._controller = controller
        self._stop_fn = stop_fn
        self.ttl_seconds = ttl_seconds
        self.interval_seconds = interval_seconds
        # Required, not defaulted: passing None means "trust this process's
        # local view alone", which is precisely the assumption behind the
        # 2026-07-31 incident. Only a caller that KNOWS it is the sole process
        # running agents (tests, single-process deployments) may pass None,
        # and making it say so out loud is the point.
        self._is_busy = is_busy
        # Consecutive passes in which every judged candidate came back
        # "could not tell". Drives the repeat warning and the L2 field.
        self._blind_passes = 0

    async def _report_pass(self, stats: dict, reaped: int) -> None:
        """Publish one pass's outcome to the L2 surfaces. Never raises."""
        global _LAST_PASS
        blind = stats.get("blind", 0)
        judged = stats.get("judged", 0)
        wholly_blind = judged > 0 and blind == judged
        self._blind_passes = self._blind_passes + 1 if wholly_blind else 0
        _LAST_PASS = {
            "reaped": reaped,
            "veto_installed": self._is_busy is not None,
            "blind_passes": self._blind_passes,
            **stats,
        }
        if not wholly_blind:
            return
        # Every candidate unreadable ⇒ nothing will EVER be culled while this
        # lasts. Repeated on a schedule so the state outlives a log rotation,
        # and mirrored into the audit table so it outlives the container.
        if (self._blind_passes - 1) % _BLIND_WARN_EVERY == 0:
            logger.warning(
                f"[reaper] liveness unreadable for all {judged} candidate(s) — "
                f"NO executor has been culled for {self._blind_passes} pass(es). "
                f"Check {RECORDING_DISABLED_ENV} and DB reachability; idle "
                f"executors accumulate until the broker refuses new ones."
            )
        # Only the kill-switch cause can land this row — when the DB is the
        # thing that is unreadable, the write needs the client that just
        # failed. That asymmetry is why the warning above exists too.
        await _audit(EVENT_CULL_DISABLED, detail={
            "judged": judged, "blind_passes": self._blind_passes,
        })

    async def reap_once(self) -> list[str]:
        """One cull pass. Returns the users whose executors were stopped.

        A stop failure for one user is logged and skipped; it never aborts the
        pass. Every path that claims a user without stopping it hands the idle
        stamp back — claiming is destructive, and this reaper is the only
        thing culling idle executors (the broker's label-based reaper collects
        ORPHANS, and a known user's container is not one), so a dropped stamp
        is a container that is never reclaimed.
        """
        users = await self._controller.claim_idle_users(
            self.ttl_seconds, is_busy=self._is_busy,
        )
        reaped: list[str] = []
        for user_id in users:
            try:
                # Re-checked per user, not once for the batch. claim_idle_users
                # vetoed everyone up front, but the stops run sequentially and
                # each `docker stop` waits out a SIGTERM grace — so by the time
                # user N is stopped its verdict can be minutes old, ample for a
                # bus-triggered run to have started and reached step 3 on that
                # very container. One indexed read against the whole bug
                # recurring at a lower rate.
                if self._is_busy is not None and await self._is_busy(user_id):
                    logger.info(
                        f"[reaper] user={user_id} became busy between the claim "
                        f"and the stop; leaving its executor alone"
                    )
                    # The claim already took its idle stamp, and just skipping
                    # would be the "claimed-then-skipped" leak claim_idle_users
                    # warns about — a user driven mostly from another process
                    # never gets a new stamp in THIS one.
                    await self._controller.restamp_idle(user_id)
                    continue
                await self._stop_fn(user_id)
                reaped.append(user_id)
            except Exception as e:  # noqa: BLE001 — best-effort, must not abort
                logger.warning(f"[reaper] failed to stop executor user={user_id}: {e}")
                # Same reasoning as the busy branch above, and the likelier of
                # the two: stop_fn is an HTTP call to the broker, so a deploy
                # restart or a 5xx lands here. Without the restamp the user's
                # stamp is gone for good and — for a user driven from another
                # process — its container is never reclaimed.
                #
                # Inside its own guard: restamp_idle only touches memory today,
                # but this handler's contract is that ONE user's failure never
                # aborts the pass, and that must not depend on what a
                # collaborator does later.
                try:
                    await self._controller.restamp_idle(user_id)
                except Exception as re:  # noqa: BLE001
                    logger.warning(f"[reaper] restamp failed user={user_id}: {re}")
        if reaped:
            logger.info(f"[reaper] reaped {len(reaped)} idle executor(s): {reaped}")
        # Only the veto sees the candidates; the claim hands back survivors
        # only, so an empty pass is ambiguous without this.
        if isinstance(self._is_busy, _CullVeto):
            await self._report_pass(self._is_busy.take_pass_stats(), len(reaped))
        return reaped

    async def run_forever(self) -> None:
        logger.info(
            f"[reaper] started (ttl={self.ttl_seconds}s interval={self.interval_seconds}s)"
        )
        while True:
            await asyncio.sleep(self.interval_seconds)
            try:
                await self.reap_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[reaper] reap pass error: {e}")


def _on_reaper_done(task: "asyncio.Task") -> None:
    # Incident lesson #2: a fire-and-forget task must surface its death.
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(f"[reaper] background task died: {exc!r}")


def maybe_start_executor_reaper() -> Optional["asyncio.Task"]:
    """Start the reaper as a background task — cloud + broker only.

    No-op (returns None) on local/desktop, or whenever no broker is
    configured: there are no per-user executors to cull there.
    """
    from xyz_agent_context.agent_framework.loop.broker_client import broker_url, stop_executor

    if not broker_url():
        return None
    ttl = int(os.getenv("EXECUTOR_IDLE_TTL_SEC", "") or DEFAULT_IDLE_TTL_SEC)
    interval = int(os.getenv("EXECUTOR_REAP_INTERVAL_SEC", "") or DEFAULT_INTERVAL_SEC)

    reaper = ExecutorReaper(
        get_admission_controller(), stop_executor,
        # This process is not the only one running agents.
        is_busy=_CullVeto(),
        ttl_seconds=ttl, interval_seconds=interval,
    )
    task = asyncio.create_task(reaper.run_forever())
    task.add_done_callback(_on_reaper_done)
    return task
