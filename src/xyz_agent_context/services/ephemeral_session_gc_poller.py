"""
@file_name: ephemeral_session_gc_poller.py
@author: NarraNexus
@date: 2026-06-11
@description: Background TTL cleanup for external-API ephemeral sessions.

External API protocol (v0.3) — Step 8.

Purpose
-------
Two complementary forces govern ephemeral-session storage:

1. **Integrators main-line clean up via DELETE** — when a session ends
   (browser close, conversation timeout, manual sign-out), the
   integrator calls `DELETE /v1/external/agents/{a}/sessions/{s}`. Best
   case: storage scales with active session count, not lifetime count.

2. **Reality main-lines NOT clean up** — integrators have bugs, deploys
   that drop in-flight cleanups, retries that swallow 200-on-already-
   deleted, browsers that close mid-stream without firing beforeunload.
   Without a safety net the `users` table grows unboundedly.

This poller is the safety net. It scans every agent that has
`external_session_ttl_seconds` set (NULL means "I don't want TTL,
trusting the integrator") and cascade-deletes any ephemeral user_id whose
last activity exceeds that TTL.

Design constraints (Owner's v0.3 decisions)
-------------------------------------------
- **No system-side minimum TTL.** If an agent owner sets TTL = 60s,
  that's what they get. Documentation in the design doc warns this
  could nuke an active conversation; the system trusts the owner's
  numbers.
- **NULL TTL = poller skips the agent entirely.** No silent cleanup
  ever fires for an agent whose owner hasn't opted in.
- **last_activity = MAX(agent_messages.updated_at) for the user_id.**
  If no messages have been sent yet, fall back to users.create_time.
  This avoids treating a user that was provisioned but never chatted
  as immediately stale.

Architecture
------------
Same pattern as MemoryConsolidationWorker: one worker per process,
single asyncio.Task in a poll loop, `start()` / `stop()` lifecycle.
Survives DB blips by logging and continuing — never raises out of the
loop.

Usage
-----
    uv run python -m xyz_agent_context.services.ephemeral_session_gc_poller

Or programmatically:

    poller = EphemeralSessionGCPoller(db_client)
    await poller.start()
    ...
    await poller.stop()
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger

from xyz_agent_context.utils.user_cascade import delete_user_cascade


class EphemeralSessionGCPoller:
    """Polls for stale ephemeral-session users and cascade-deletes them.

    Defaults to a 5-minute interval — chat traffic timescales are
    minutes-to-hours, so polling every 5 minutes catches stale sessions
    well before they accumulate into a storage problem, without
    spinning the DB.
    """

    POLL_INTERVAL_SECONDS = 5 * 60  # 5 minutes

    def __init__(
        self,
        db_client: Any,
        *,
        poll_interval: Optional[float] = None,
    ):
        self._db = db_client
        self.poll_interval = poll_interval or self.POLL_INTERVAL_SECONDS
        self.running = False
        self._task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            "[ephemeral_session_gc] poller started "
            "(poll_interval={}s)",
            self.poll_interval,
        )

    async def stop(self) -> None:
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("[ephemeral_session_gc] poller stopped")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _run_loop(self) -> None:
        while self.running:
            try:
                await self.run_one_pass()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "[ephemeral_session_gc] run_one_pass failed: {}", exc
                )
            await asyncio.sleep(self.poll_interval)

    async def run_one_pass(self) -> dict:
        """Single pass: scan opt-in agents → check each ephemeral user
        against TTL → cascade-delete the expired ones.

        Returns a stats dict (`{agents_scanned, users_examined,
        users_deleted}`) for observability — caller (or test) can
        assert against it.
        """
        # Step 1: which agents have opted into TTL?
        agents = await self._db.get(
            "agents",
            filters={},  # no native "IS NOT NULL" filter — Python side
            limit=10_000,
        )
        ttl_agents = [
            a for a in agents
            if a.get("external_session_ttl_seconds") not in (None, "")
        ]

        if not ttl_agents:
            return {"agents_scanned": 0, "users_examined": 0, "users_deleted": 0}

        stats = {
            "agents_scanned": len(ttl_agents),
            "users_examined": 0,
            "users_deleted": 0,
        }

        now = datetime.now(timezone.utc)

        for agent in ttl_agents:
            agent_id = agent["agent_id"]
            ttl_seconds = int(agent["external_session_ttl_seconds"])

            users = await self._db.get(
                "users",
                filters={"owned_by_agent": agent_id},
                limit=10_000,
            )
            stats["users_examined"] += len(users)

            for user in users:
                user_id = user["user_id"]
                last_activity = await self._last_activity(user_id, user)
                if last_activity is None:
                    # No timestamp at all — should never happen since
                    # users.create_time is NOT NULL with a default, but
                    # if it does, skip rather than delete a row we
                    # can't reason about.
                    logger.warning(
                        "[ephemeral_session_gc] user_id={!r} has no "
                        "last_activity timestamp; skipping",
                        user_id,
                    )
                    continue

                age_seconds = (now - last_activity).total_seconds()
                if age_seconds < ttl_seconds:
                    continue

                logger.info(
                    "[ephemeral_session_gc] user_id={!r} age={}s "
                    "exceeds TTL={}s for agent_id={!r}; cascade deleting",
                    user_id, int(age_seconds), ttl_seconds, agent_id,
                )
                cascade = await delete_user_cascade(user_id, self._db)
                stats["users_deleted"] += 1 if cascade.get("users", 0) == 1 else 0

        logger.info(
            "[ephemeral_session_gc] pass complete: scanned={} agents, "
            "examined={} users, deleted={} users",
            stats["agents_scanned"],
            stats["users_examined"],
            stats["users_deleted"],
        )
        return stats

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _last_activity(
        self, user_id: str, user_row: dict
    ) -> Optional[datetime]:
        """Return last activity for an ephemeral user.

        Definition: MAX(events.updated_at) WHERE user_id = ?.
        Fallback: users.create_time. Returning None means "we have no
        idea" and the caller will skip GC.

        agent_messages doesn't have user_id (channel-class table); the
        per-session message ledger is `events`, same source the list
        sessions endpoint uses for its freshness column.
        """
        rows = await self._db.execute(
            "SELECT MAX(updated_at) AS m FROM events WHERE user_id = ?",
            (user_id,),
        )
        max_msg = rows[0].get("m") if rows else None

        if max_msg:
            return _parse_dt(max_msg)

        return _parse_dt(user_row.get("create_time"))


# Small utility — mirrors the date parser other repositories use, kept
# private here so we don't need to depend on a sibling module that might
# not be present in stripped-down deploys.
def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        # Try ISO 8601 with timezone first.
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
        # Fall back to SQLite's `YYYY-MM-DD HH:MM:SS`.
        try:
            return datetime.strptime(
                value, "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------


async def _main() -> None:
    """Standalone entry for `uv run python -m
    xyz_agent_context.services.ephemeral_session_gc_poller`.

    Useful in production where we want this worker isolated from the
    FastAPI process. Backend can also start it via its lifespan handler
    if you prefer single-process deployments.
    """
    from xyz_agent_context.utils.db_factory import get_db_client

    db = await get_db_client()
    poller = EphemeralSessionGCPoller(db)
    await poller.start()

    # Hold the process open until the worker is cancelled. KeyboardInterrupt
    # (Ctrl-C) flows through to stop()
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        await poller.stop()


if __name__ == "__main__":
    asyncio.run(_main())
