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
  - run_recorder.user_has_live_run — IS the user actually idle (DB truth)
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
container out from under a live group-chat reply, which surfaced to the
user as ``infra_transient``. The idle claim is therefore vetoed by a
cross-process liveness check against the ``events`` table, which every
process writes to.
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

StopFn = Callable[[str], Awaitable[None]]

DEFAULT_IDLE_TTL_SEC = 1200   # 20 min (locked decision)
DEFAULT_INTERVAL_SEC = 120


async def cross_process_busy_check(user_id: str) -> bool:
    """True when this user has a live run in ANY process — the veto the
    reaper hands to ``claim_idle_users``.

    Reads the ``events`` table because that is the only place every
    process's runs meet (incident lesson #5: a DB trace outlives the
    process that wrote it, and "is it there?" is answerable, unlike a log
    grep). A skip is recorded to the executor audit log rather than logged
    only: "how often did we nearly cull a live run" is the L3 measurement
    that says whether this guard is doing anything.

    Never raises, and every failure path answers "busy" — not knowing must
    never authorise a cull (binding rule #14).
    """
    from xyz_agent_context.agent_runtime.run_recorder import user_has_live_run

    try:
        from xyz_agent_context.utils.db.db_factory import get_db_client

        db = await get_db_client()
    except Exception as e:  # noqa: BLE001 — no DB ⇒ no verdict ⇒ do not cull
        logger.warning(f"[reaper] busy check unavailable for user={user_id}: {e}")
        return True

    busy = await user_has_live_run(db, user_id)
    if busy:
        logger.info(
            f"[reaper] skipping user={user_id}: a run is live in another "
            f"process (idle here, busy elsewhere)"
        )
        await _audit_cull_skipped(db, user_id)
    return busy


async def _audit_cull_skipped(db, user_id: str) -> None:
    """Best-effort audit row for a vetoed cull. Never raises — the observer
    must not break the pass it is observing."""
    try:
        from xyz_agent_context.repository.executor_audit_repository import (
            ExecutorAuditRepository,
        )
        from xyz_agent_context.schema.executor_audit import EVENT_CULL_SKIPPED_BUSY

        await ExecutorAuditRepository(db).record(
            event_type=EVENT_CULL_SKIPPED_BUSY, user_id=user_id,
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
        ttl_seconds: float = DEFAULT_IDLE_TTL_SEC,
        interval_seconds: float = DEFAULT_INTERVAL_SEC,
        is_busy: Optional[BusyCheck] = None,
    ) -> None:
        self._controller = controller
        self._stop_fn = stop_fn
        self.ttl_seconds = ttl_seconds
        self.interval_seconds = interval_seconds
        # None = trust the controller's local view alone. Only safe when the
        # process holding this reaper is the ONLY one that runs agents (tests,
        # single-process deployments). Production wiring in
        # ``maybe_start_executor_reaper`` always injects the check.
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
        ttl_seconds=ttl, interval_seconds=interval,
        # Never omit: this process is not the only one running agents.
        is_busy=cross_process_busy_check,
    )
    task = asyncio.create_task(reaper.run_forever())
    task.add_done_callback(_on_reaper_done)
    return task
