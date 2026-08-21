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

StopFn = Callable[[str], Awaitable[None]]

DEFAULT_IDLE_TTL_SEC = 1200   # 20 min (locked decision)
DEFAULT_INTERVAL_SEC = 120

# Stand-in run id for "we could not find out". Not a real run, but it gets a
# real run's answer — hands off. Never audited: the two causes (the recording
# kill switch is pulled, or the DB is unreachable) are log-and-alert
# territory, and one of them cannot write a row anyway.
UNKNOWN_RUN = "unknown"

# Callers already warned that run recording is off. Bounded (two callers),
# and reset when the switch goes back on so a second pull warns again.
_recording_off_warned: set[str] = set()


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

    async def __call__(self, user_id: str) -> bool:
        run_id = await self._check(user_id)
        if run_id is None:
            self._blocked_by.pop(user_id, None)
            return False
        if self._blocked_by.get(user_id) != run_id:
            if len(self._blocked_by) >= self._MAX_TRACKED:
                self._blocked_by.pop(next(iter(self._blocked_by)))
            self._blocked_by[user_id] = run_id
            logger.info(
                f"[reaper] skipping user={user_id}: run {run_id} is live in "
                f"another process (idle here, busy elsewhere)"
            )
            if run_id != UNKNOWN_RUN:
                await _audit_cull_skipped(user_id, run_id)
        return True


async def _audit_cull_skipped(user_id: str, run_id: str) -> None:
    """Best-effort audit row for a vetoed cull. Never raises — the observer
    must not break the pass it is observing."""
    try:
        from xyz_agent_context.repository.executor_audit_repository import (
            ExecutorAuditRepository,
        )
        from xyz_agent_context.schema.executor_audit import EVENT_CULL_SKIPPED_BUSY
        from xyz_agent_context.utils.db.db_factory import get_db_client

        await ExecutorAuditRepository(await get_db_client()).record(
            event_type=EVENT_CULL_SKIPPED_BUSY, user_id=user_id, run_id=run_id,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[reaper] cull-skip audit failed user={user_id}: {e}")


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

    async def reap_once(self) -> list[str]:
        """One cull pass. Returns the users whose executors were stopped.

        A stop failure for one user is logged and skipped (the broker's own
        label-based reaper backstops orphans); it never aborts the pass.
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
        if reaped:
            logger.info(f"[reaper] reaped {len(reaped)} idle executor(s): {reaped}")
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
