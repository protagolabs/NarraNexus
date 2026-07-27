"""
@file_name: executor_reaper.py
@author:
@date: 2026-06-17
@description: Idle-cull coordinator for per-user Executor containers.

Pure coordinator (dependency-injected): it owns neither the concurrency
state nor the docker transport. It periodically asks the admission
controller which users have gone idle past the TTL, and asks a ``stop_fn``
(the broker client) to stop them. This keeps the three concerns separate:
  - AgentAdmissionController — concurrency + idle bookkeeping
  - ExecutorReaper          — WHEN to cull (this file)
  - broker_client.stop_executor — HOW to stop (docker transport)

Binding rule #14: only idle executors (zero active loops) are ever
reaped — a running loop is never interrupted. The cull just delays the
next start by a cold boot, surfaced to the user via the "waking up" UX.
"""
from __future__ import annotations

import asyncio
import os
from typing import Awaitable, Callable, Optional

from loguru import logger

from xyz_agent_context.agent_runtime.admission import (
    AgentAdmissionController,
    get_admission_controller,
)

StopFn = Callable[[str], Awaitable[None]]

DEFAULT_IDLE_TTL_SEC = 1200   # 20 min (locked decision)
DEFAULT_INTERVAL_SEC = 120


class ExecutorReaper:
    """Periodically stops executors whose user has been idle past the TTL."""

    def __init__(
        self,
        controller: AgentAdmissionController,
        stop_fn: StopFn,
        *,
        ttl_seconds: float = DEFAULT_IDLE_TTL_SEC,
        interval_seconds: float = DEFAULT_INTERVAL_SEC,
        post_reap_fn: Optional[StopFn] = None,
    ) -> None:
        self._controller = controller
        self._stop_fn = stop_fn
        self.ttl_seconds = ttl_seconds
        self.interval_seconds = interval_seconds
        # Optional per-user hook fired AFTER a user's idle executor is stopped.
        # Used to revoke that user's orphaned free-tier gateway session keys:
        # the user is idle (zero active loops), so any ACTIVE key is a crash
        # orphan and safe to revoke (铁律 #14 — never touches a live run).
        self._post_reap_fn = post_reap_fn

    async def reap_once(self) -> list[str]:
        """One cull pass. Returns the users whose executors were stopped.

        A stop failure for one user is logged and skipped (the broker's own
        label-based reaper backstops orphans); it never aborts the pass.
        """
        users = await self._controller.claim_idle_users(self.ttl_seconds)
        reaped: list[str] = []
        for user_id in users:
            try:
                await self._stop_fn(user_id)
                reaped.append(user_id)
            except Exception as e:  # noqa: BLE001 — best-effort, must not abort
                logger.warning(f"[reaper] failed to stop executor user={user_id}: {e}")
                continue  # executor may still be alive — do NOT revoke its keys
            # Executor stopped → user has no live loop → revoke orphaned keys.
            if self._post_reap_fn is not None:
                try:
                    await self._post_reap_fn(user_id)
                except Exception as e:  # noqa: BLE001 — best-effort, must not abort
                    logger.warning(f"[reaper] post-reap hook failed user={user_id}: {e}")
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

    # Free-tier gateway cleanup: when a user's idle executor is culled, revoke
    # any orphaned gateway session keys they left behind (crash between mint and
    # the agent_loop finally). No-op unless the gateway is configured.
    post_reap_fn: Optional[StopFn] = None
    if os.environ.get("SYSTEM_DEFAULT_LLM_GATEWAY_URL", "").strip():
        async def _revoke_user_gateway_keys(user_id: str) -> None:
            from xyz_agent_context.utils.db.db_factory import get_db_client
            from xyz_agent_context.agent_framework.providers.gateway_key_service import (
                GatewayKeyService,
            )
            db = await get_db_client()
            svc = GatewayKeyService.from_env(db)
            if svc is not None:
                await svc.revoke_all_for_user(user_id)

        post_reap_fn = _revoke_user_gateway_keys

    reaper = ExecutorReaper(
        get_admission_controller(), stop_executor,
        ttl_seconds=ttl, interval_seconds=interval,
        post_reap_fn=post_reap_fn,
    )
    task = asyncio.create_task(reaper.run_forever())
    task.add_done_callback(_on_reaper_done)
    return task
