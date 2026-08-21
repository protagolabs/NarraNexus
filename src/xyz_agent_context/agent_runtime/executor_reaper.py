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
container out from under a live group-chat reply, which surfaced to the
user as ``infra_transient``. The idle claim is therefore vetoed by a
cross-process liveness check against the ``events`` table, which every
process writes to.
"""
from __future__ import annotations

import asyncio
import os
from contextlib import contextmanager, nullcontext
from typing import Awaitable, Callable, Optional

from loguru import logger

from xyz_agent_context.agent_runtime.admission import (
    AgentAdmissionController,
    AgingBusyCheck,
    BusyCheck,
    get_admission_controller,
)
from xyz_agent_context.agent_runtime.run_recorder import RECORDING_DISABLED_ENV

StopFn = Callable[[str], Awaitable[None]]

DEFAULT_IDLE_TTL_SEC = 1200   # 20 min (locked decision)
DEFAULT_INTERVAL_SEC = 120

# Stand-in run ids for "we could not find out". Not real runs, but the answer
# is the one a real run gives — hands off. Two distinct values because the two
# causes need different responses from whoever reads the audit trail: one is a
# switch somebody deliberately pulled, the other is an outage. A single
# "unknown" would leave the WHY the comment claims to preserve unrecoverable.
_UNKNOWN_RECORDING_OFF = "unknown:recording-off"
_UNKNOWN_DB_UNAVAILABLE = "unknown:db-unavailable"


# (caller, user) pairs already warned about while the recording switch is on.
# Without this, step 3's per-turn call reprints the same line every turn, on
# the one occasion somebody is reading these logs to debug something else.
_recording_off_warned: set[tuple[str, str]] = set()


async def live_run_elsewhere(
    user_id: str,
    *,
    exclude_run_id: Optional[str] = None,
    caller: str = "reaper",
) -> Optional[str]:
    """The id of a run live in ANY process for this user, or None.

    THE cross-process "is this user busy?" call for everything that wants to
    destroy or stop a container. Reads the ``events`` table because that is
    the only place every process's runs meet (incident lesson #5: a DB trace
    outlives the process that wrote it, and "is the row there?" is
    answerable, unlike a log grep).

    Never raises. Every failure path answers "busy" — with a SENTINEL id, so
    callers that log or audit the answer can say which kind of busy it was.
    Not knowing must never authorise destroying anything (binding rule #14).

    ``caller`` only labels the logs: the two consumers suffer DIFFERENT
    consequences when the answer is unknowable (culling stops vs. executor
    images stop rolling), and a line that names the wrong one sends the next
    person debugging in the wrong direction.
    """
    from xyz_agent_context.agent_runtime.run_recorder import (
        first_live_run_id,
        recording_enabled,
    )

    if not recording_enabled():
        # The kill switch that turns off trigger-path run recording turns off
        # exactly the runs this guard exists to protect: without a recorder
        # their events row never flips to 'running', so the DB would report
        # them idle. An observability switch must not silently become a
        # licence to destroy containers, so while it is on nothing is
        # reapable and no stale image is replaced.
        #
        # Both costs are real and neither may be traded away: allowing
        # replacement here would mean destroying containers with NO view of
        # in-flight runs at all, which is this whole change in reverse.
        if (caller, user_id) not in _recording_off_warned:
            _recording_off_warned.add((caller, user_id))
            logger.warning(
                f"[{caller}] run recording is disabled ({RECORDING_DISABLED_ENV}) "
                f"— cross-process liveness is unknowable for user={user_id}. "
                f"Executor idle-culling is OFF and stale executor images will "
                f"NOT roll while it stays disabled."
            )
        return _UNKNOWN_RECORDING_OFF
    # Switch is back on: forget, so a SECOND pull warns again. Recording is
    # toggled precisely when somebody is debugging, these processes live for
    # weeks, and for the stale-replace half this log is the only signal there
    # is (that path writes no audit row). Also keeps the set from growing with
    # the user base.
    _recording_off_warned.discard((caller, user_id))
    try:
        from xyz_agent_context.utils.db.db_factory import get_db_client

        db = await get_db_client()
        return await first_live_run_id(db, user_id, exclude_run_id=exclude_run_id)
    except Exception as e:  # noqa: BLE001 — no verdict ⇒ assume busy
        logger.warning(f"[{caller}] busy check unavailable for user={user_id}: {e}")
        return _UNKNOWN_DB_UNAVAILABLE


async def stale_replacement_is_safe(
    user_id: str, *, active_run_id: Optional[str] = None
) -> bool:
    """Whether the broker may destroy this user's container to roll a stale
    executor image — the verdict step 3 hands to ``ensure_executor``.

    The broker cannot answer this itself and should not learn how: it is the
    one component with docker access and its threat model rests on having
    exactly one caller-controlled input (a user_id it validates). Handing it
    DB credentials to look up run state would widen that surface for a fact
    the orchestrator already holds.

    ``active_run_id`` is the asking run's own id, excluded from the count —
    at ensure() time the caller's events row is already ``running`` but it
    has not connected to the container yet, so counting itself would mean
    "never replace", and a stale executor after a wire-protocol change
    degrades runs silently (2026-07: an old executor got an EMPTY MCP set).

    Deliberately conservative: a live run of the same user may not be using
    the executor at all (not yet at step 3, or a direct-trigger run that
    never does). Deferring costs one more turn on old code and self-corrects
    at the next ensure; replacing under a live run kills it (rule #14).
    """
    return await live_run_elsewhere(
        user_id, exclude_run_id=active_run_id, caller="stale-replace",
    ) is None


class _CullVeto:
    """The reaper's ``is_busy`` veto, with per-RUN audit de-duplication.

    A vetoed user KEEPS its idle stamp (that is the whole point — see
    ``claim_idle_users``), so it is re-offered every pass and re-vetoed every
    pass for as long as its run lives. Auditing each of those would make the
    row count a function of RUN DURATION: one legitimate 10-hour agent
    (binding rule #14 says that is normal) would write ~300 rows and read as
    hundreds of near-misses. The metric counts runs saved, so each (user, run)
    is recorded once.

    De-duplicated on the RUN ID, not on pass membership. A user stops being a
    candidate whenever it goes active in THIS process (``acquire`` pops its
    idle stamp), so "forget everyone absent from this pass" would forget a
    still-live blocking run and re-audit it later: chat in backend, group-chat
    run still going in workers, chat ends, TTL elapses, second row for the
    same run. Mixed-mode users are exactly the incident's trigger profile, so
    that is the population the metric would over-count.

    Bounded by dropping a user once it has been absent from ``_FORGET_AFTER``
    consecutive passes: entries cannot outlive the executors they describe.
    """

    # Passes a user may be absent before its dedup entry is dropped. >1 so a
    # user that merely went active here for a moment keeps its entry.
    _FORGET_AFTER = 10

    # Hard ceiling, independent of the aging above. Aging only advances when
    # the caller brackets each pass with pass_(), and nothing enforces that at
    # merge time (see AgingBusyCheck). Without the cap, forgetting would leak
    # in a process that lives for weeks; with it, forgetting costs a few
    # duplicate audit rows. Sized well above any plausible number of
    # simultaneously-vetoed users.
    _MAX_TRACKED = 4096

    def __init__(self, check=live_run_elsewhere) -> None:
        self._check = check
        # user_id -> (run_id that blocked us, pass number we last saw it in).
        # ONE structure on purpose: a companion "seen this pass" set would be
        # reset only inside pass_(), so in the very scenario _MAX_TRACKED
        # exists for — nobody brackets — it would grow unboundedly while this
        # one stayed capped, and the cap would be a half-measure the comments
        # around it claimed was whole.
        self._blocked_by: dict[str, tuple[str, int]] = {}
        self._pass_no: int = 0

    @contextmanager
    def pass_(self):
        """Bracket one reaper pass. Exception-safe: aging happens on entry, so
        it is unaffected by how the pass ends."""
        self._pass_no += 1
        for user_id, (_run, last_seen) in list(self._blocked_by.items()):
            # Strictly greater: aging runs at pass ENTRY, before this pass has
            # had any chance to re-veto the user. With >=, an entry last seen
            # in pass N dies at the start of pass N+_FORGET_AFTER — having
            # been absent for only _FORGET_AFTER-1 passes, one short of what
            # the name and every comment describing it promise.
            if self._pass_no - last_seen > self._FORGET_AFTER:
                del self._blocked_by[user_id]
        yield self

    async def __call__(self, user_id: str) -> bool:
        run_id = await self._check(user_id)
        if run_id is None:
            self._blocked_by.pop(user_id, None)
            return False
        previous = self._blocked_by.get(user_id)
        if previous is None and len(self._blocked_by) >= self._MAX_TRACKED:
            # Evict the least recently seen; its run is the likeliest to be over.
            stalest = min(self._blocked_by, key=lambda u: self._blocked_by[u][1])
            del self._blocked_by[stalest]
        self._blocked_by[user_id] = (run_id, self._pass_no)
        if previous is None or previous[0] != run_id:
            logger.info(
                f"[reaper] skipping user={user_id}: run {run_id} is live in "
                f"another process (idle here, busy elsewhere)"
            )
            if run_id != _UNKNOWN_DB_UNAVAILABLE:
                # Writing this row needs the very DB client that just failed,
                # so the attempt is guaranteed to fail too — a second dead
                # round-trip per user per pass, and a warning that says
                # nothing the first one did not. During a DB outage the
                # authoritative signals are the busy-check log line and the
                # fact that culling stopped; see executor_audit.py on how to
                # read (and not over-read) this metric.
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
        # local view alone", which is precisely the assumption that caused the
        # 2026-07-31 incident. Only a caller that KNOWS it is the sole process
        # running agents (tests, single-process deployments) may pass None, and
        # making it say so out loud is the point.
        self._is_busy = is_busy

    async def reap_once(self) -> list[str]:
        """One cull pass. Returns the users whose executors were stopped.

        A stop failure for one user is logged and skipped (the broker's own
        label-based reaper backstops orphans); it never aborts the pass.
        """
        veto = self._is_busy
        # A veto may or may not carry state across calls; only the stateful
        # kind has a pass to bracket. Narrowed against the declared protocol
        # rather than sniffed with hasattr, so the contract is written down —
        # though see AgingBusyCheck: nothing enforces it at merge time, which
        # is why _CullVeto bounds its own state instead of trusting this call.
        with (veto.pass_() if isinstance(veto, AgingBusyCheck) else nullcontext()):
            users = await self._controller.claim_idle_users(
                self.ttl_seconds, is_busy=veto,
            )
        reaped: list[str] = []
        for user_id in users:
            try:
                # Re-check per user, not once for the batch. claim_idle_users
                # vetoed everyone up front, but the stops run sequentially and
                # each `docker stop` waits out a SIGTERM grace — so by the time
                # user N is stopped its verdict can be minutes old, ample for a
                # bus-triggered run to have started and reached step 3 on that
                # very container. Cheap (one indexed read) against the cost of
                # the whole bug recurring at a lower rate.
                if self._is_busy is not None and await self._is_busy(user_id):
                    logger.info(
                        f"[reaper] user={user_id} became busy between the claim "
                        f"and the stop; leaving its executor alone"
                    )
                    # The claim already took its idle stamp, and skipping here
                    # would be the very "claimed-then-skipped" leak that
                    # claim_idle_users' docstring warns about: a user driven
                    # mostly from another process never gets a new stamp in
                    # THIS one, so its container would never be reconsidered.
                    # setdefault, so a genuinely older stamp is not pushed back.
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
