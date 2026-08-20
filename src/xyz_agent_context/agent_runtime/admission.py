"""
@file_name: admission.py
@author:
@date: 2026-06-17
@description: Two-level concurrency admission control for agent runs.

A single user can drive MANY agents at once (chat + scheduled jobs +
message-bus interactions), so without a gate the box OOMs. This is the
control-plane gate that bounds it (binding rule #14 compliant: it only
ever DELAYS the start of a run by queueing — it NEVER interrupts a
running loop).

Two caps + a memory guard (all env-tunable, calibrated for a 64G host):
  - MAX_CONCURRENT_USERS   (global)  — distinct users with ≥1 active loop
  - MAX_LOOPS_PER_USER     (per-user)— one user's simultaneous loops
  - MAX_CONCURRENT_LOOPS   (global)  — total loops; the real RAM ceiling
  - MIN_FREE_MEM_MB        (dynamic) — hold new loops when free RAM is low

A run is admitted only when ALL hold; otherwise it waits. The per-user
cap is the main anti-starvation lever (no user can exceed M); a fully
fair round-robin out-queue is a future refinement.

State lives behind this controller instance (a seam) so it can move to
Redis when the orchestrator scales to >1 replica (binding rule #20). For
now it is an in-process asyncio controller — which means its view is
PARTIAL: the cloud orchestrator runs as backend + workers, so neither
process's counters know about the other's runs. That is harmless for
admission (each process caps its own share) but NOT for the idle
bookkeeping the reaper consumes, which is why ``claim_idle_users`` takes
an out-of-process ``is_busy`` veto.

Disabled (all caps unlimited, no mem guard) in local/desktop so
``bash run.sh`` and the DMG behave exactly as before (binding rule #7);
enabled with the 64G defaults in cloud. Env vars override either way.
"""
from __future__ import annotations

import asyncio
import math
import os
import time
from contextlib import asynccontextmanager
from contextlib import AbstractContextManager
from typing import Callable, Optional, Protocol

from loguru import logger

class BusyCheck(Protocol):
    """Injected cross-process veto for idle claiming: "is this user busy
    somewhere else?". See ``claim_idle_users``.

    ``pass_`` is optional and lets a stateful veto bracket one claim pass
    (the reaper's uses it to age its audit de-duplication). Declared here
    rather than sniffed at the call site with ``hasattr`` so a typo degrades
    into a type error instead of silently skipping the aging — and the thing
    that stops being aged grows without bound.
    """

    async def __call__(self, user_id: str) -> bool: ...

    def pass_(self) -> AbstractContextManager: ...  # optional

# Simultaneous in-flight vetoes per claim pass.
_VETO_CONCURRENCY = 8
# Whole-batch budget. Exceeding it yields NO claims (everyone reads as busy),
# so a wedged DB stalls culling instead of stalling the reaper itself.
_VETO_BATCH_TIMEOUT_S = 60.0


def _free_mem_mb() -> float:
    """Available RAM in MB, or +inf when it can't be read (non-Linux)."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1024.0
    except Exception:  # noqa: BLE001
        pass
    return math.inf


def _opt_int_env(name: str, default: Optional[int]) -> Optional[int]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        v = int(raw)
        return v if v > 0 else None  # 0 / negative = unlimited
    except ValueError:
        return default


class AgentAdmissionController:
    """In-process two-level admission gate (global + per-user + mem guard)."""

    def __init__(
        self,
        max_users: Optional[int],
        max_loops_per_user: Optional[int],
        max_loops_global: Optional[int],
        min_free_mem_mb: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_users = max_users
        self.max_loops_per_user = max_loops_per_user
        self.max_loops_global = max_loops_global
        self.min_free_mem_mb = min_free_mem_mb
        self._clock = clock
        self._cond = asyncio.Condition()
        self._global = 0
        self._per_user: dict[str, int] = {}
        # user_id -> monotonic time it dropped to zero active loops. Present
        # ONLY while a user is idle; the executor reaper consumes this.
        self._idle_since: dict[str, float] = {}
        # Number of coroutines currently blocked inside acquire() waiting for
        # a slot — observable via snapshot() for L2 monitoring.
        self._waiting: int = 0

    @property
    def enabled(self) -> bool:
        return any(
            x is not None
            for x in (self.max_users, self.max_loops_per_user, self.max_loops_global)
        ) or self.min_free_mem_mb > 0

    def _active_users(self) -> int:
        return sum(1 for v in self._per_user.values() if v > 0)

    def _can_admit(self, user_id: str) -> bool:
        cur_user = self._per_user.get(user_id, 0)
        if self.max_loops_global is not None and self._global >= self.max_loops_global:
            return False
        if self.max_loops_per_user is not None and cur_user >= self.max_loops_per_user:
            return False
        if (
            self.max_users is not None
            and cur_user == 0
            and self._active_users() >= self.max_users
        ):
            return False
        if self.min_free_mem_mb > 0 and _free_mem_mb() < self.min_free_mem_mb:
            return False
        return True

    def snapshot(self) -> dict:
        """Return a plain-dict snapshot of current admission state.

        No lock is held — this is a best-effort monitoring read. Each
        field is read atomically (CPython GIL), so the snapshot is
        internally consistent enough for L2 observability; it is NOT
        transactionally consistent with an in-flight acquire/release.
        """
        free = _free_mem_mb()
        return {
            "active_users": self._active_users(),
            "active_loops": self._global,
            "queue_depth": self._waiting,
            "max_users": self.max_users,
            "max_loops_per_user": self.max_loops_per_user,
            "max_loops_global": self.max_loops_global,
            "min_free_mem_mb": self.min_free_mem_mb,
            "free_mem_mb": int(free) if free != math.inf else None,
            "per_user_loops": dict(self._per_user),
            "enabled": self.enabled,
        }

    async def restamp_idle(self, user_id: str) -> None:
        """Re-mark a user idle after a claim that was not acted on.

        ``claim_idle_users`` is destructive; a caller that claims and then
        decides not to stop the executor would otherwise drop the stamp for
        good (see that method's docstring for why that leaks). setdefault, not
        assignment: an existing, older stamp is the truthful one, and
        overwriting it would grant the user a free extra TTL.
        """
        async with self._cond:
            if user_id not in self._per_user:
                self._idle_since.setdefault(user_id, self._clock())

    async def acquire(self, user_id: str) -> str:
        """Wait (queue) until this run may start, then reserve a slot.

        Returns a token to pass back to ``release``. Never interrupts —
        only the START is delayed (binding rule #14).
        """
        async with self._cond:
            self._waiting += 1
            try:
                await self._cond.wait_for(lambda: self._can_admit(user_id))
            finally:
                self._waiting -= 1
            self._global += 1
            self._per_user[user_id] = self._per_user.get(user_id, 0) + 1
            self._idle_since.pop(user_id, None)  # active again → not idle
        return user_id

    async def release(self, token: str) -> None:
        async with self._cond:
            self._global = max(0, self._global - 1)
            if token in self._per_user:
                self._per_user[token] -= 1
                if self._per_user[token] <= 0:
                    del self._per_user[token]
                    self._idle_since[token] = self._clock()  # went idle now
            self._cond.notify_all()

    async def claim_idle_users(
        self,
        ttl_seconds: float,
        is_busy: Optional[BusyCheck] = None,
    ) -> list[str]:
        """Return + un-track users idle for >= ttl_seconds. Claiming is
        DESTRUCTIVE — a returned user loses its idle stamp.

        A user is "idle" once THIS PROCESS's active-loop count hits zero
        (stamped in release). Returned users are removed from idle tracking
        under the lock so the reaper can stop their executor without
        double-reaping; if a new run arrives afterwards the broker just
        cold-starts a fresh container. Users with active loops are never
        returned (rule #14 — we never reap a running loop).

        ``is_busy`` is the CROSS-PROCESS veto. This controller is a
        per-process singleton, so "zero active loops here" does not mean the
        user is idle — backend cannot see runs alive in workers and vice
        versa. The caller injects an out-of-process truth source (the reaper
        passes ``executor_reaper.live_run_elsewhere``); any user it vetoes is
        skipped.

        A vetoed user KEEPS its idle stamp, which is why the check lives in
        here rather than in the caller's filter: claiming is destructive, so
        a caller that claimed-then-skipped would drop the stamp and the user
        would never be reconsidered until its next release in THIS process —
        for a user driven mostly from another process, that is never, and the
        container leaks forever.

        The veto runs OUTSIDE the lock (it may do I/O — the reaper's hits the
        DB) so admission never stalls behind it; the second pass re-checks
        each candidate's stamp is still the one we saw, so a user that became
        active meanwhile is not claimed.
        """
        async with self._cond:
            now = self._clock()
            candidates = [
                (u, ts) for u, ts in self._idle_since.items() if now - ts >= ttl_seconds
            ]
        if not candidates:
            return []

        busy: set[str] = set()
        if is_busy is not None:
            # Bounded fan-out: one veto is one DB round-trip, and the candidate
            # count is caller-driven (everyone who crossed the TTL since the
            # last pass). Unbounded, a burst would contend with live requests
            # for the same pool — the recurring shape in this codebase is
            # "caller-controlled input with no cardinality bound".
            gate = asyncio.Semaphore(_VETO_CONCURRENCY)

            async def _judge(u: str) -> bool:
                async with gate:
                    return await is_busy(u)

            try:
                # A hung DB pool would otherwise park this gather forever, and
                # with it reap_once and the whole reaper loop — silently, with
                # no exception for the done-callback to report (incident
                # lesson #4: liveness needs more than "the task still exists").
                # A veto budget, not a cap on anything a user's agent does.
                verdicts = await asyncio.wait_for(
                    asyncio.gather(
                        *(_judge(u) for u, _ in candidates),
                        return_exceptions=True,
                    ),
                    timeout=_VETO_BATCH_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    f"[admission] veto batch timed out after "
                    f"{_VETO_BATCH_TIMEOUT_S}s for {len(candidates)} candidate(s) "
                    f"— treating all as busy"
                )
                return []
            for (u, _), verdict in zip(candidates, verdicts):
                # An unusable verdict means we do not know → do not reap.
                if isinstance(verdict, BaseException) or verdict:
                    busy.add(u)

        async with self._cond:
            claimed: list[str] = []
            for u, ts in candidates:
                if u in busy:
                    continue
                # Re-activated (acquire popped the stamp) or re-released
                # (release wrote a new one) while the veto was in flight.
                if self._idle_since.get(u) != ts:
                    continue
                del self._idle_since[u]
                claimed.append(u)
            return claimed

    @asynccontextmanager
    async def slot(self, user_id: str):
        token = await self.acquire(user_id)
        try:
            yield
        finally:
            await self.release(token)


_controller: Optional[AgentAdmissionController] = None


def _build_from_env() -> AgentAdmissionController:
    """Cloud → 64G-calibrated defaults; local/desktop → unlimited (rule #7).
    Env vars override in either mode."""
    try:
        from xyz_agent_context.utils.deployment_mode import get_deployment_mode
        is_cloud = get_deployment_mode() == "cloud"
    except Exception:  # noqa: BLE001
        is_cloud = False

    if is_cloud:
        d_users, d_per_user, d_global, d_mem = 20, 5, 50, 6144
    else:
        d_users, d_per_user, d_global, d_mem = None, None, None, 0

    return AgentAdmissionController(
        max_users=_opt_int_env("MAX_CONCURRENT_USERS", d_users),
        max_loops_per_user=_opt_int_env("MAX_LOOPS_PER_USER", d_per_user),
        max_loops_global=_opt_int_env("MAX_CONCURRENT_LOOPS", d_global),
        min_free_mem_mb=int(os.environ.get("MIN_FREE_MEM_MB", str(d_mem)) or d_mem),
    )


def get_admission_controller() -> AgentAdmissionController:
    """Process-wide singleton (the seam that could become Redis-backed)."""
    global _controller
    if _controller is None:
        _controller = _build_from_env()
    return _controller


def reset_admission_controller_for_test(controller: Optional[AgentAdmissionController] = None) -> None:
    """Test hook — inject a controller or clear the singleton."""
    global _controller
    _controller = controller
