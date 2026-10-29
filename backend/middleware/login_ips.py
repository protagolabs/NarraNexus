"""
@file_name: login_ips.py
@author: Bin Liang
@date: 2026-10-29
@description: Record which public client IPs each user is actively authenticating
from, for the sentinel's abuse IP<->account linkage (instance_login_ips).

The sentinel (deploy repo) auto-bans a scanning client IP at the edge; to ban the
account behind it too, it reads this table: "which accounts were active from this
IP in the last N hours". Two write paths feed it through this one module:

- login (``netmind-login``) — ``record_login_ip`` writes unconditionally;
- every authenticated request — ``note_authenticated_request`` (called from the
  auth middleware) writes in the background, at most once per
  ``RECORD_THROTTLE_SEC`` per (user, ip).

The second path is what makes the linkage actually work. A JWT lives 7 days and
is portable across addresses: recording only at login would leave ``last_seen``
pinned to the mint time (a 24h reader window then finds nobody) and would never
see the IP a token is REPLAYED from — exactly the attacker who logs in on one
address and scans from a VPS.

Guards:
- Only globally routable IPs are recorded. A non-public "client IP" means the
  proxy-hop count (``FUNNEL_TRUSTED_PROXY_HOPS``) no longer matches the topology;
  recording it would fill the table with nginx's address and silently blind the
  linkage, so the login path warns (cloud only — local runs are loopback by
  design) and the per-request path skips quietly.
- Idle rows are pruned after ``RETENTION_SEC`` (opportunistically, at most once
  per ``_PRUNE_INTERVAL_SEC`` per process). Retention must stay well above the
  sentinel's link window (24h by default) or the reader goes blind; it is NOT
  tied to account deletion — a banned-then-deleted attacker's IPs staying for
  the retention period is deliberate (forensics), and the period bounds it.
- Everything is best-effort: a recording failure never breaks a login or a
  request, and the background task carries a done-callback so its failure is
  logged instead of vanishing.

The throttle map is an in-process OPTIMIZATION only (losing it costs one extra
upsert), bounded by ``_THROTTLE_MAX_ENTRIES``; it is not shared state that needs
a cross-process seam.
"""
from __future__ import annotations

import asyncio
import ipaddress
from time import time as wall_clock
from typing import Any, Callable

from loguru import logger

from narranexus.kernel.deployment import is_cloud_mode

TABLE = "instance_login_ips"
# These two values are the contract the reader (deploy repo, nexus_sentinel
# app/account_linker.py WRITER_REFRESH_SEC / WRITER_RETENTION_SEC) mirrors to
# reject an ACCESS_LINK_WINDOW_SEC outside (refresh, retention) at boot.
# Change them together.
#: authenticated activity refreshes a (user, ip) row at most this often
RECORD_THROTTLE_SEC = 600
#: rows idle longer than this are pruned (≫ the sentinel's 24h link window)
RETENTION_SEC = 30 * 86_400
_PRUNE_INTERVAL_SEC = 3_600
_THROTTLE_MAX_ENTRIES = 50_000

_last_recorded: dict[tuple[str, str], float] = {}
_last_prune: float = 0.0
# Strong references: asyncio keeps only weak refs to tasks, so an unreferenced
# background write could be garbage-collected mid-flight.
_background_tasks: set[asyncio.Task] = set()


def is_recordable_ip(ip: str) -> bool:
    """True only for a globally routable address (not private / loopback /
    CGNAT / doc range / the client_ip() fallbacks "-" or a hostname)."""
    try:
        return ipaddress.ip_address((ip or "").strip()).is_global
    except ValueError:
        return False


def _mark(user_id: str, ip: str, now: float) -> None:
    if len(_last_recorded) >= _THROTTLE_MAX_ENTRIES:
        cutoff = now - RECORD_THROTTLE_SEC
        for key in [k for k, t in _last_recorded.items() if t < cutoff]:
            del _last_recorded[key]
        if len(_last_recorded) >= _THROTTLE_MAX_ENTRIES:
            _last_recorded.clear()
    _last_recorded[(user_id, ip)] = now


def _spawn(coro) -> asyncio.Task:
    """Background task with a strong ref and a failure log (CLAUDE.md lesson #2)."""
    task = asyncio.get_running_loop().create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    task.add_done_callback(_log_task_failure)
    return task


async def _prune(db_client: Any, cutoff: int) -> None:
    try:
        await db_client.execute(
            f"DELETE FROM {TABLE} WHERE last_seen < %s",
            params=(cutoff,),
            fetch=False,
        )
    except Exception:  # noqa: BLE001 — retention is housekeeping, never a failure path
        logger.opt(exception=True).warning("[login-ips] prune failed")


def _maybe_prune(db_client: Any, now: int) -> None:
    """Schedule the retention DELETE at most once per interval, in the
    background: the login path awaits record_login_ip and must not wait on
    housekeeping. The clock is stamped BEFORE the attempt so a persistently
    failing DELETE is retried hourly, not on every call."""
    global _last_prune
    if now - _last_prune < _PRUNE_INTERVAL_SEC:
        return
    _last_prune = now
    _spawn(_prune(db_client, now - RETENTION_SEC))


async def record_login_ip(db_client: Any, user_id: str, ip: str) -> bool:
    """Upsert (user_id, ip, last_seen=now). Returns True when a row was written.

    ``pair_key`` matches the table's single-column UNIQUE conflict target;
    ``last_seen`` is epoch seconds (what the cross-process sentinel compares);
    ``created_at`` is left out so a conflict keeps the first-seen time.
    Never raises.
    """
    if not is_recordable_ip(ip):
        if is_cloud_mode():
            logger.warning(
                f"[login-ips] non-public client ip {ip!r} for user={user_id} — "
                "proxy-hop count likely out of sync with the edge; not recorded"
            )
        return False
    now = int(wall_clock())
    try:
        await db_client.upsert(
            TABLE,
            {
                "user_id": user_id,
                "ip": ip,
                "pair_key": f"{user_id}|{ip}",
                "last_seen": now,
            },
            "pair_key",
        )
    except Exception:  # noqa: BLE001 — recording must never break the caller
        logger.opt(exception=True).warning(f"[login-ips] record failed user={user_id}")
        return False
    _mark(user_id, ip, now)
    _maybe_prune(db_client, now)
    return True


async def _record_in_background(user_id: str, ip: str) -> None:
    # Resolved at call time (not imported at module load) so the same
    # get_db_client the auth middleware uses is the one that is called.
    from narranexus.platform.utils.db.db_factory import get_db_client

    written = False
    try:
        db_client = await get_db_client()
        written = await record_login_ip(db_client, user_id, ip)
    finally:
        if not written:
            # Release the claim taken in note_authenticated_request so a
            # transient failure does not blind this pair for the whole
            # throttle window — the next request retries.
            _last_recorded.pop((user_id, ip), None)


def _log_task_failure(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.opt(exception=exc).warning("[login-ips] background record failed")


def note_authenticated_request(user_id: str, ip: str) -> None:
    """Auth-middleware hook: cheap and non-blocking.

    Schedules a background upsert only when this (user, ip) has not been
    recorded within ``RECORD_THROTTLE_SEC``. The pair is claimed BEFORE
    scheduling so a burst of concurrent requests schedules one write, not many.
    """
    if not user_id or not is_recordable_ip(ip):
        return
    now = wall_clock()
    last = _last_recorded.get((user_id, ip))
    if last is not None and now - last < RECORD_THROTTLE_SEC:
        return
    _mark(user_id, ip, now)
    _spawn(_record_in_background(user_id, ip))


async def _presence_loop(user_id: str, ip: str, is_alive: Callable[[], bool]) -> None:
    while True:
        await asyncio.sleep(RECORD_THROTTLE_SEC)
        if not is_alive():
            return
        note_authenticated_request(user_id, ip)


def start_connection_presence(
    user_id: str, ip: str, is_alive: Callable[[], bool]
) -> asyncio.Task | None:
    """Keep (user, ip) fresh for the life of a long connection.

    A WebSocket carries one agent run, and runs may last tens of hours (CLAUDE.md
    rule #14); noting the pair only at connect would let ``last_seen`` age out of
    the reader's window mid-run. The task re-notes every ``RECORD_THROTTLE_SEC``
    while ``is_alive()`` and exits on its own within one interval after the
    connection closes, so the caller needs no teardown. Covers every WS branch
    (new run, reconnect, cross-process follow) from one call site.
    """
    if not user_id or not is_recordable_ip(ip):
        return None
    return _spawn(_presence_loop(user_id, ip, is_alive))


__all__ = [
    "RECORD_THROTTLE_SEC",
    "RETENTION_SEC",
    "TABLE",
    "is_recordable_ip",
    "note_authenticated_request",
    "record_login_ip",
    "start_connection_presence",
]
