"""
@file_name: main.py
@author: NetMind.AI
@date: 2025-11-28
@description: FastAPI application entry point

Provides WebSocket streaming for agent runtime and REST APIs for
jobs, inbox, agents, and awareness management.

Usage:
    uvicorn backend.main:app --reload --port 8000
"""

import asyncio
import time
import os
import sys
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from loguru import logger

from xyz_agent_context.utils.logging import setup_logging
from xyz_agent_context.utils.db.db_factory import get_db_client, close_db_client
from backend.config import settings
from backend.auth import _is_cloud_mode, assert_jwt_secret_safe


# Budget for /health's database round-trip. Must stay comfortably under the
# container healthcheck's own `timeout: 5s` (stacks/narranexus-app/compose.yml)
# — if the probe outlives the healthcheck, docker records a timeout instead of
# our "unhealthy + reason" body and the reason is lost.
_HEALTH_DB_TIMEOUT_SEC = 3.0

# How long a probe result is reused. `/health` is public and unauthenticated
# (backend.auth's allowlist; nginx passes it straight through), so without this
# anyone can make the backend spend a pooled connection per request — and the
# pool is 10. A few hundred requests a second would hold every connection while
# real traffic queued on `pool.acquire()`: an endpoint added to make failure
# visible, turned into a way to cause it. Worse, that is most available exactly
# during an incident, when the endpoint is most likely to be probed.
#
# 5s is well under the container healthcheck's `interval: 30s`, so any single
# prober still gets a fresh round-trip on every one of its own polls. Two
# probers landing within 5s of each other will have the later one served from
# cache — acceptable, and the reason the window is far smaller than the poll
# interval rather than merely smaller.
#
# Must stay GREATER than `_HEALTH_DB_TIMEOUT_SEC`. Callers queued on the probe
# lock re-check the cache when they get in; if the entry they are waiting for
# has already expired by then, each of them probes again and the single-flight
# turns into a serial train of timeouts — the amplification it was added to
# remove, rebuilt.
_HEALTH_CACHE_TTL_SEC = 5.0

# (probe_started_at, monotonic_deadline, ok, detail).
#
# The cache only answers requests that arrive AFTER a result is published; the
# window between a miss and that publication is covered by `_probe_lock()`
# below. (An earlier version of this comment argued a lock "would buy nothing"
# — that was written before anyone worked out how long the window is when the
# database is slow, which is the whole timeout.)
#
# The write is guarded by when the probe STARTED, not when it finished. With the
# lock in place two probes should not overlap, but the guard is two lines and it
# is what stops a stale verdict if the lock is ever bypassed: a slow failing
# probe that began before a fast succeeding one would otherwise overwrite the
# good result on arrival and keep reporting unhealthy for a further TTL after
# the database had recovered (and the reverse, hiding a fresh failure).
_health_cache: "tuple[float, float, bool, str] | None" = None

# Single-flight around the probe itself. The cache alone only stops requests
# that arrive AFTER a result has been published; between a miss and that
# publication every arrival ran its own probe and held its own pooled
# connection. How long that window is depends on how slow the database is —
# milliseconds when healthy, the full `_HEALTH_DB_TIMEOUT_SEC` when not. So the
# cache closed the amplification exactly in the case that never mattered, and
# left it open in the one it was added for.
#
# A plain Lock, not a background refresh task: the "fire-and-forget hazard"
# argument applies to spawning tasks, not to an `asyncio.Lock`, which creates
# none. Waiters here hold no database connection.
#
# One lock PER EVENT LOOP, not one module-level lock. `asyncio.Lock.acquire`
# pins itself to the running loop the first time it is actually CONTENDED
# (CPython `locks.py`, `_LoopBoundMixin._get_loop`), and raises
# "is bound to a different event loop" for every later contention on a
# different one. This repo does swap loops in-process — `db_factory` carries a
# whole eviction path for it — and a `/health` that raises there becomes a 500,
# which the container healthcheck turns into unhealthy, which (per the
# deployment coupling documented on `health`) fails `docker compose up`
# outright.
#
# Keyed by `id(loop)` with a strong reference alongside, which is the shape
# `db_factory._locks_by_loop` already uses for exactly this problem — same data
# structure, same purpose, same lifetime question. A WeakKeyDictionary would
# read more neatly and does work today (uvloop 0.22.1's Loop is weakref-able,
# verified), but production runs uvloop only because `uvicorn[standard]` pulls
# it in; betting the availability of the whole deployment on an undeclared
# transitive dependency keeping an undocumented property is not a bet worth
# taking for the syntax.
#
# The `is not` comparison is load-bearing, not defensive: `id()` is reused after
# a loop is collected, and handing a new loop a lock already pinned to a dead
# one reproduces the exact RuntimeError this exists to prevent.
_health_probe_locks: "dict[int, asyncio.Lock]" = {}
_health_probe_loops: "dict[int, asyncio.AbstractEventLoop]" = {}


def _probe_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    key = id(loop)
    if _health_probe_loops.get(key) is not loop:
        # New loop (or a reused id). Drop entries for loops that are gone before
        # adding: holding them strongly is what makes `id()` safe, but a process
        # that churns loops — every pytest-asyncio test does — would otherwise
        # accumulate one lock and one dead loop per loop, forever.
        for dead in [k for k, lp in _health_probe_loops.items() if lp.is_closed()]:
            _health_probe_loops.pop(dead, None)
            _health_probe_locks.pop(dead, None)
        _health_probe_locks[key] = asyncio.Lock()
        _health_probe_loops[key] = loop
    return _health_probe_locks[key]


def _detect_bind_host() -> str:
    """Detect actual uvicorn bind host.

    uvicorn CLI `--host` is NOT exposed via env vars; therefore we check:
    (a) sys.argv for `--host <host>` or `--host=<host>` (covers `uvicorn ...` CLI)
    (b) DASHBOARD_BIND_HOST env var (set by launcher scripts as a redundant signal)
    (c) default '127.0.0.1' if neither present
    """
    argv = sys.argv
    for i, arg in enumerate(argv):
        if arg == "--host" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--host="):
            return arg.split("=", 1)[1]
    return os.environ.get("DASHBOARD_BIND_HOST", "127.0.0.1")


def _assert_local_bind_is_loopback(is_cloud_mode: bool) -> None:
    """Fail-fast in local mode if backend is bound to non-loopback.

    Rationale: dashboard returns real user content (events.final_output, sender names).
    Local mode assumes single-user trust on loopback; binding 0.0.0.0 exposes PII to LAN.
    See design doc TDR-12 + security critic C-1.

    Manyfold deployment override (Owner spec 2026-05-25 §4.8): when
    ``ENABLE_MANYFOLD_API=1`` the platform's ingress is the only path in
    and gateway-token Bearer auth is the security boundary — 0.0.0.0 bind
    is intentional and required. Skip the assertion in that mode.
    """
    if is_cloud_mode:
        return
    if os.environ.get("ENABLE_MANYFOLD_API", "").strip() in ("1", "true", "yes"):
        logger.info(
            "Manyfold mode active — skipping local-bind loopback assertion "
            "(MANYFOLD_GATEWAY_TOKEN is the security boundary)."
        )
        return
    if os.environ.get("RUNTIME_MODE", "").strip() == "container":
        # Container deployments inherently bind 0.0.0.0 (the Docker
        # network namespace IS the security boundary). The loopback
        # check is for laptops on shared LANs, not containers.
        logger.info("Container mode active — skipping local-bind loopback assertion.")
        return
    host = _detect_bind_host()
    if host not in ("127.0.0.1", "localhost", "::1"):
        logger.critical(f"Local mode requires loopback bind; detected host={host!r}. Exiting.")
        sys.exit(1)


def _warn_if_multi_worker() -> None:
    """Warn if WEB_CONCURRENCY>1 — active_sessions registry is process-local.

    See design doc TDR-1 / ARK-1: multi-worker deployments undercount concurrent
    sessions. Must upgrade to Redis-backed SessionRegistry in that scenario.
    """
    try:
        workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
    except ValueError:
        workers = 1
    if workers > 1:
        logger.warning(
            f"WEB_CONCURRENCY={workers}: dashboard active_sessions registry "
            "undercounts (process-local). Upgrade to a Redis-backed registry "
            "if multi-worker is required."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager

    Handles startup and shutdown events:
    - Startup: Initialize database connection pool
    - Shutdown: Close database connections
    """
    # Startup
    setup_logging("backend")
    logger.info("Starting FastAPI application...")

    # Dashboard v2 TDR-12: fail-fast if local mode is not bound to loopback
    _assert_local_bind_is_loopback(is_cloud_mode=_is_cloud_mode())
    # Fail-fast in cloud mode if JWT_SECRET is unset / left at the public default
    # (a known signing secret = anyone can forge any user's token).
    assert_jwt_secret_safe()
    _warn_if_multi_worker()

    # Initialize database connection pool
    logger.info("Initializing database connection pool...")
    db = await get_db_client()
    logger.info("Database connection pool initialized")

    # Auto-migrate schema (unified: works for both SQLite and MySQL via backend)
    from xyz_agent_context.utils.db.schema_registry import auto_migrate

    await auto_migrate(db._backend)
    logger.info("Schema auto-migration complete")

    # Provider Unification (Phase 0) — backfill new columns on legacy
    # user_providers rows. Idempotent + cheap; runs every boot so a row
    # added by an older codebase gets classified the moment we start.
    from xyz_agent_context.agent_framework.providers.driver import (
        backfill_provider_metadata,
    )

    await backfill_provider_metadata(db)

    # Agent Runtime Lifecycle (Phase C) — initialize the in-memory
    # active_runs registry and settle stale 'running' rows.
    #
    # The registry is empty on every process start, but that does NOT
    # mean every running row is stale: trigger runs (lark / team / job)
    # are recorded from OTHER processes and stay alive across a backend
    # restart. Liveness is therefore judged by heartbeat freshness
    # (run_recorder.sweep_stale_runs / run_is_live — the one shared
    # rule), and the sweep repeats periodically so a run orphaned
    # mid-flight in ANY process settles within ~90s + one interval,
    # not only when the backend happens to restart.
    import asyncio as _asyncio

    from xyz_agent_context.agent_runtime.run_recorder import (
        HEARTBEAT_INTERVAL_S,
        sweep_stale_runs,
    )

    app.state.active_runs = {}
    await sweep_stale_runs(db)

    async def _stale_run_sweeper() -> None:
        while True:
            await _asyncio.sleep(HEARTBEAT_INTERVAL_S * 2)
            await sweep_stale_runs(db)

    app.state.stale_run_sweep_task = _asyncio.create_task(_stale_run_sweeper())
    app.state.stale_run_sweep_task.add_done_callback(
        lambda t: (
            logger.warning(f"[run-sweep] loop exited: {t.exception()}")
            if not t.cancelled() and t.exception() is not None
            else None
        )
    )

    # One-shot data migrations (idempotent; run after schema migration)
    from xyz_agent_context.utils.one_shot_migrations import (
        heal_legacy_singleton_ownership,
        migrate_jobs_protocol_v2_timezone,
    )

    migration_stats = await migrate_jobs_protocol_v2_timezone(db)
    if migration_stats.get("cancelled"):
        logger.warning(
            f"[migration] Cancelled {migration_stats['cancelled']} pre-v2 jobs "
            f"lacking timezone field; users will need to recreate them."
        )

    # Self-heal pre-2026-05-13 local-mode singleton-ownership bug. Non-
    # technical users hit "can't add agent to my own team" because team
    # rows were created with owner_user_id='local-default' instead of
    # their real user_id. This idempotently re-attributes those rows
    # when (and only when) the user identity is unambiguous. See
    # one_shot_migrations.py for the safety conditions.
    try:
        heal_stats = await heal_legacy_singleton_ownership(db)
        if heal_stats.get("teams"):
            logger.info(f"[singleton-heal] re-attributed {heal_stats['teams']} legacy team(s)")
    except Exception as e:  # noqa: BLE001
        # Self-heal is best-effort — never block startup on it.
        logger.warning(f"[singleton-heal] skipped due to error: {e}")

    # Versioned, run-once data migrations (the layer-by-layer upgrade ledger).
    # Runs in EVERY environment (cloud / bash run.sh / DMG sidecar all boot this
    # lifespan), applying every still-pending migration in order — so a DB that
    # skipped versions catches up one layer at a time. Best-effort: never block
    # startup on a migration error (search degrades gracefully; it retries next
    # startup). See backend/migrations/.
    try:
        from backend.migrations import run_pending_migrations

        migrated = await run_pending_migrations(db)
        if migrated:
            logger.info(f"[migrate] applied {len(migrated)} pending migration(s): {list(migrated)}")
    except Exception as e:  # noqa: BLE001 — data migration must never block startup
        logger.error(f"[migrate] migration runner skipped due to error: {e}")

    # Provider resolution. One tree for every caller (see providers/resolver);
    # the free tier is an ordinary provider card, so nothing extra is wired for
    # it here beyond the wallet client the routes build on demand.
    from xyz_agent_context.agent_framework.providers.free_tier import (
        is_free_tier_enabled,
    )
    from xyz_agent_context.agent_framework.providers.resolver import (
        ProviderResolver,
    )
    from xyz_agent_context.agent_framework.providers.user_service import (
        UserProviderService,
    )
    from xyz_agent_context.repository.user_repository import UserRepository

    app.state.user_repository = UserRepository(db)
    app.state.provider_resolver = ProviderResolver(UserProviderService(db))
    logger.info(f"Provider resolution wired (free tier={is_free_tier_enabled()})")

    # Unified Agent Memory — start the background consolidation worker
    # (design 2026-06-03 §7.4). Drains the dirty-scope queue and distils raw
    # observations into consolidated memory out of the turn's path. Opportunistic
    # background work — never caps the agent loop (iron rule #14).
    from xyz_agent_context.services.memory_consolidation_worker import (
        MemoryConsolidationWorker,
    )

    memory_worker = MemoryConsolidationWorker(db)
    await memory_worker.start()
    app.state.memory_consolidation_worker = memory_worker
    logger.info("Memory consolidation worker started")

    # Team bulletin — keep each team's progress summary fresh so a member
    # joining a long task does not have to reconstruct it from scrollback.
    # Same opportunistic contract as the memory worker: per-team isolation, a
    # failure keeps the previous summary, and nothing ever waits on it
    # (iron rule #14).
    from xyz_agent_context.services.team_summary_worker import TeamSummaryWorker

    team_summary_worker = TeamSummaryWorker(db)
    await team_summary_worker.start()
    app.state.team_summary_worker = team_summary_worker
    logger.info("Team summary worker started")

    # Per-user Executor idle-cull reaper (cloud + broker only; no-op
    # otherwise). Stops executor containers whose user has gone idle past
    # the TTL — only idle ones, never a running loop (iron rule #14).
    from xyz_agent_context.agent_runtime.executor_reaper import (
        maybe_start_executor_reaper,
    )

    app.state.executor_reaper_task = maybe_start_executor_reaper()
    if app.state.executor_reaper_task is not None:
        logger.info("Executor idle-cull reaper started")

    # Marketplace seeds — populate this registry host's catalog + store.
    # OFF the startup critical path: the team seed fetches over the network
    # (narra.nexus, up to ~minutes if slow) and both seeds do S3 I/O, so
    # awaiting them before `yield` would freeze startup and can exceed the
    # compose healthcheck start_period. Fire-and-forget with a done-callback.
    async def _seed_marketplaces() -> None:
        try:
            from xyz_agent_context.marketplace.team_marketplace_service import TeamMarketplaceService

            if not TeamMarketplaceService()._is_registry_host():
                return  # a pure desktop client proxies to the cloud
            from xyz_agent_context.marketplace._team_marketplace_seed import (
                seed_team_marketplace,
            )

            seeded = await seed_team_marketplace(db)
            logger.info(f"Team Marketplace seed: {seeded} templates present")

            # First-party skills vendored in marketplace/resources/marketplace_skills/ (incl. the
            # default NetMind vision/audio fallbacks) — without this a fresh
            # deploy has an empty Skills tab and default-skill install finds
            # nothing to auto-install on agent creation.
            from xyz_agent_context.marketplace._skill_marketplace_seed import (
                seed_skill_marketplace,
            )

            skill_seeded = await seed_skill_marketplace(db)
            logger.info(f"Skill Marketplace seed: {skill_seeded} first-party skill(s) present")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[marketplace-seed] skipped due to error: {e}")

    app.state.marketplace_seed_task = _asyncio.create_task(_seed_marketplaces())
    app.state.marketplace_seed_task.add_done_callback(
        lambda t: (
            logger.warning(f"[marketplace-seed] task died: {t.exception()}")
            if not t.cancelled() and t.exception() is not None
            else None
        )
    )

    # Warm the model price table off the event loop.
    #
    # `import litellm` costs ~1.5s and the table is loaded lazily on first use.
    # That first use is inside `await record_cost(...)`, i.e. on the loop — so
    # without this the first priced call of a process stalls every concurrent WS
    # frame for a second and a half. A thread, not a task: the cost is a
    # synchronous import, so create_task would still block. Fire-and-forget with
    # a done callback (never a bare create_task — iron-rule lesson #2); a
    # failure here only means the table loads lazily later, which is the old
    # behaviour, so it warns rather than raising.
    async def _warm_price_table() -> None:
        from xyz_agent_context.utils import model_pricing

        await _asyncio.to_thread(model_pricing.warm_cache)

    app.state.price_table_warm_task = _asyncio.create_task(_warm_price_table())
    app.state.price_table_warm_task.add_done_callback(
        lambda t: (
            logger.warning(f"[pricing] price-table warm failed: {t.exception()}")
            if not t.cancelled() and t.exception() is not None
            else None
        )
    )

    # Skill reconciler — keeps the skill_installations audit table following
    # the filesystem truth (users can hand-edit skills/; the DB heals). The
    # loop does its first reconcile pass immediately, so we do NOT block
    # startup on it here (reconcile_all scans every workspace + hashes every
    # installed skill — latency grows with users).
    from xyz_agent_context.services.skill_sync_service import SkillSyncService

    skill_sync = SkillSyncService(db)
    app.state.skill_sync_task = _asyncio.create_task(skill_sync.run_forever())
    app.state.skill_sync_task.add_done_callback(
        lambda t: (
            logger.warning(f"[skill-sync] loop exited: {t.exception()}")
            if not t.cancelled() and t.exception() is not None
            else None
        )
    )
    logger.info("Skill reconciler started")

    yield

    # Shutdown
    logger.info("Shutting down FastAPI application...")
    skill_sync_task = getattr(app.state, "skill_sync_task", None)
    if skill_sync_task is not None:
        skill_sync_task.cancel()
    sweep_task = getattr(app.state, "stale_run_sweep_task", None)
    if sweep_task is not None:
        sweep_task.cancel()
    seed_task = getattr(app.state, "marketplace_seed_task", None)
    if seed_task is not None:
        seed_task.cancel()
    reaper_task = getattr(app.state, "executor_reaper_task", None)
    if reaper_task is not None:
        reaper_task.cancel()
    worker = getattr(app.state, "memory_consolidation_worker", None)
    if worker is not None:
        await worker.stop()
    # Stopped BEFORE the db client closes: its poll loop holds that client, and
    # a pass landing mid-teardown would log a confusing connection error on
    # every clean shutdown.
    summary_worker = getattr(app.state, "team_summary_worker", None)
    if summary_worker is not None:
        await summary_worker.stop()
    await close_db_client()
    logger.info("Database connections closed")

    # Flush any enqueue=True records still in the multiprocessing queue
    # before the interpreter exits — otherwise the last few lines (the
    # ones describing the actual shutdown) get dropped.
    await logger.complete()


# Create FastAPI application
# In cloud mode, don't expose the interactive API docs / schema — Swagger UI,
# ReDoc and openapi.json hand an attacker the full endpoint surface. Developers
# still get them in local/dev mode.
_docs_kwargs = (
    {"docs_url": None, "redoc_url": None, "openapi_url": None}
    if _is_cloud_mode()
    else {}
)
app = FastAPI(
    title="Agent Context API",
    description="WebSocket streaming and REST APIs for Agent Context runtime",
    version="1.0.0",
    lifespan=lifespan,
    **_docs_kwargs,
)

# Middleware order is LIFO: the LAST registration is the OUTERMOST layer
# and runs FIRST per request. Registration order below therefore yields
#
#     CORS  ->  access_log  ->  auth  ->  routes
#
# and every response, including ones short-circuited deep inside, unwinds
# back out through all three.
#
# Two constraints are encoded here, both learned the hard way:
#
# 1. access_log must wrap auth, so a 401/402 that never reaches a route
#    still produces an access line.
# 2. CORS must wrap EVERYTHING. It used to be registered first, which made
#    it the innermost layer — so when auth_middleware returned a 401
#    directly, CORSMiddleware never ran and that response carried no
#    Access-Control-Allow-Origin header. Cross-origin callers (any
#    deployment where the SPA is not served from the API's own origin)
#    then had the browser discard the response outright: `fetch` rejects
#    with a TypeError and the client cannot see the status, let alone the
#    body. Every 401-handling behaviour on the frontend — reading the
#    `code` to decide whether the session is dead, and before that even
#    noticing the 401 at all — was silently dead code there.
from backend.auth import auth_middleware
from backend.middleware.access_log import access_log_middleware

app.middleware("http")(auth_middleware)
app.middleware("http")(access_log_middleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Renders route-level AuthError as {detail, code} instead of FastAPI's
# default {detail} — the `code` is what stops the frontend from treating
# "your NetMind token is stale" as "your session is dead". See
# backend/auth_errors.py.
from backend.auth_errors import install_auth_error_handler

install_auth_error_handler(app)


# Import and include routers
from backend.routes.websocket import router as websocket_router
from backend.routes.agents.core import router as agents_router
from backend.routes.agents.artifacts import router as agents_artifacts_router
from backend.routes.artifacts.users import router as users_artifacts_router
from backend.routes.jobs import router as jobs_router
from backend.routes.runs import router as runs_router
from backend.routes.auth import router as auth_router
from backend.routes.skills import router as skills_router
from backend.routes.marketplace_skills import router as marketplace_skills_router
from backend.routes.marketplace_teams import router as marketplace_teams_router
from backend.routes.home_assistant import router as home_assistant_router
from backend.routes.providers import router as providers_router
from backend.routes.inbox import router as inbox_router
from backend.routes.notices import router as notices_router
from backend.routes.dashboard.routes import router as dashboard_router
from backend.routes.channels.lark import router as lark_router
from backend.routes.channels.slack import router as slack_router
from backend.routes.channels.telegram import router as telegram_router
from backend.routes.channels.wechat import router as wechat_router
from backend.routes.channels.narramessenger import router as narramessenger_router
from backend.routes.channels.discord import router as discord_router
from backend.routes.quota import router as quota_router
from backend.routes.admin.quota import router as admin_quota_router
from backend.routes.notifications import router as notifications_router
from backend.routes.admin.logs import router as admin_logs_router
from backend.routes.admin.migration import router as admin_migration_router
from backend.routes.admin.suspend import router as admin_suspend_router
from backend.routes.admin.runtime import router as admin_runtime_router
from backend.routes.transcription.routes import router as transcription_router
from backend.routes.transcription.public import router as transcription_public_router
from backend.routes.artifacts.public import router as artifacts_public_router
from backend.routes.office_watch.proxy import (
    router as office_watch_router,
    public_router as office_watch_public_router,
)
from backend.routes.teams import router as teams_router
from backend.routes.bundle import router as bundle_router
from backend.routes.migrate import router as migrate_router
from backend.routes.arena import router as arena_router
from backend.routes.me import router as me_router
from backend.routes.billing import router as billing_router
from backend.routes.feedback import router as feedback_router
from backend.routes.analytics import router as product_analytics_router

app.include_router(websocket_router, tags=["WebSocket"])
app.include_router(auth_router, prefix="/api/auth", tags=["Auth"])
app.include_router(agents_router, prefix="/api/agents", tags=["Agents"])
app.include_router(agents_artifacts_router, prefix="/api/agents", tags=["Artifacts"])
app.include_router(office_watch_router, prefix="/api", tags=["OfficeWatch"])
app.include_router(office_watch_public_router, prefix="/api/public", tags=["OfficeWatch"])
app.include_router(users_artifacts_router, prefix="/api/users", tags=["Artifacts"])
app.include_router(jobs_router, prefix="/api/jobs", tags=["Jobs"])
app.include_router(runs_router, prefix="/api/runs", tags=["Runs"])
app.include_router(skills_router, prefix="/api/skills", tags=["Skills"])
# /api/marketplace is one namespace, split by object: skills/* here;
# teams/* is reserved for the Team/Agent bundle marketplace.
app.include_router(
    marketplace_skills_router, prefix="/api/marketplace/skills", tags=["SkillMarketplace"]
)
app.include_router(
    marketplace_teams_router, prefix="/api/marketplace/teams", tags=["TeamMarketplace"]
)
app.include_router(home_assistant_router, prefix="/api/home-assistant", tags=["HomeAssistant"])
app.include_router(providers_router, prefix="/api/providers", tags=["Providers"])
app.include_router(teams_router, prefix="/api/teams", tags=["Teams"])
app.include_router(bundle_router, prefix="/api/bundle", tags=["Bundle"])
app.include_router(migrate_router, prefix="/api/migrate", tags=["Migration"])
app.include_router(me_router, prefix="/api/me", tags=["Me"])
app.include_router(billing_router, prefix="/api/billing", tags=["Billing"])
app.include_router(feedback_router, prefix="/api", tags=["Feedback"])
app.include_router(product_analytics_router, prefix="/api/analytics", tags=["Analytics"])
app.include_router(inbox_router, prefix="/api/agent-inbox", tags=["Inbox"])
app.include_router(notices_router, prefix="/api/notices", tags=["Notices"])
app.include_router(dashboard_router, prefix="/api/dashboard", tags=["Dashboard"])
app.include_router(lark_router, prefix="/api/lark", tags=["Lark"])
app.include_router(slack_router, prefix="/api/slack", tags=["Slack"])
app.include_router(telegram_router, prefix="/api/telegram", tags=["Telegram"])
app.include_router(wechat_router, prefix="/api/wechat", tags=["WeChat"])
app.include_router(narramessenger_router, prefix="/api/narramessenger", tags=["NarraMessenger"])
app.include_router(arena_router, tags=["Arena"])
app.include_router(discord_router, prefix="/api/discord", tags=["Discord"])
app.include_router(quota_router, tags=["Quota"])
app.include_router(admin_quota_router, tags=["AdminQuota"])
app.include_router(admin_migration_router, tags=["AdminMigration"])
app.include_router(admin_suspend_router, tags=["AdminSuspend"])
app.include_router(admin_runtime_router, tags=["AdminRuntime"])
app.include_router(notifications_router, tags=["Notifications"])
app.include_router(admin_logs_router, prefix="/api/admin/logs", tags=["AdminLogs"])
app.include_router(
    transcription_router,
    prefix="/api/transcription",
    tags=["Transcription"],
)
app.include_router(
    transcription_public_router,
    prefix="/api/public/transcription",
    tags=["TranscriptionPublic"],
)
app.include_router(
    artifacts_public_router,
    prefix="/api/public/artifacts",
    tags=["ArtifactsPublic"],
)


@app.get("/health")
async def health():
    """Detailed health check.

    ``database`` is a real round-trip, not an assertion, and reports only a
    coarse classification — this endpoint is public and unauthenticated, so the
    driver's own message (which names the database host and user) stays in the
    log. It used to be the
    literal string "connected", which meant the probe reported a healthy
    database while every request in the process was failing with
    ``InterfaceError: (0, 'Not connected')`` — the container stayed green for
    the whole 2026-08-17 outage and the monitoring built on top of it saw
    nothing. A health field that cannot fail carries no information.

    The probe deliberately goes through the ordinary client path (the same
    pool and the same task-scoped transaction lookup real handlers use) so it
    exercises what it claims to cover. It is bounded by a timeout well under
    the container healthcheck's own 5s, so a hung database surfaces as
    unhealthy rather than as a hung probe.

    Carries the team-summary worker's last pass so its liveness is observable
    from outside the process. Recording the counters and never exposing them
    would leave the same blind spot they were added for: "every room is quiet"
    and "every room is failing" both look like a worker that is simply up.

    Reported, never judged — a non-zero ``failed`` is not an unhealthy service
    (a single team with a bad provider key must not fail the container's probe),
    so ``status`` does not depend on it. The database check is the exception:
    it *does* decide the status, because a backend that cannot reach its
    database cannot serve any authenticated request.

    DEPLOYMENT COUPLING — read before changing the 503.
    The container healthcheck (deploy repo, ``stacks/narranexus-app/compose.yml``)
    fetches this endpoint with ``urllib.request.urlopen``, which raises on 5xx,
    so a 503 turns the container unhealthy. That is the point: it is what lets
    container-state monitoring see a database outage at all, which it could not
    on 2026-08-17.

    What that costs, stated in full because half of it is easy to miss: **four**
    services declare ``depends_on: backend: condition: service_healthy`` —
    ``frontend``, plus ``mcp``, ``workers`` and ``model-sync`` through the
    ``x-python-common`` anchor. So running ``docker compose up`` *while the
    database is unreachable* does not degrade partially; compose **fails the
    whole command** with "dependency failed to start: container
    narranexus-backend is unhealthy". Nothing new starts: no frontend (the ops
    Caddy loses its upstream and the public entrypoint returns 502 — the
    frontend has no host port binding of its own), and no workers, which is
    every channel trigger, the module poller, the job trigger and the message
    bus.

    Recovery order is therefore constrained to "fix the database first, then
    deploy". Cold start already required the database (lifespan builds the
    pool), so this changes redeploy-during-an-outage, not first boot; a stack
    that is already running keeps serving until the probe flips after
    ``retries: 5 x interval: 30s``.

    Accepted deliberately: the alternative — pointing the healthcheck at the
    shallow ``/healthz`` — restores the exact blind spot this endpoint was fixed
    to close. Do not "fix" the coupling by making this return 200 with an
    unhealthy body; a health field that cannot fail carries no information,
    which is how the original bug survived.
    """
    cached = _health_cache
    if cached is not None and time.monotonic() < cached[1]:
        return _health_body(cached[2], cached[3])

    # ONE budget for the whole handler, queue included. `_HEALTH_DB_TIMEOUT_SEC`
    # exists solely to stay under the container healthcheck's `timeout: 5s`, so
    # that a failure is recorded as our "503 + reason" rather than replaced by
    # docker's own "health check timed out" — losing the reason reopens the
    # blind spot this endpoint was fixed to close. Budgeting only the probe left
    # the queue wait outside it: a holder cancelled late (a client disconnect
    # mid-probe, which is the same cancellation this PR handles in
    # `transaction()`) publishes nothing, and the next waiter would start a
    # fresh full-length probe on top of what it had already spent.
    deadline = time.monotonic() + _HEALTH_DB_TIMEOUT_SEC
    lock = _probe_lock()
    acquired = False
    try:
        try:
            await asyncio.wait_for(
                lock.acquire(), timeout=max(0.0, deadline - time.monotonic())
            )
            acquired = True
        except asyncio.TimeoutError:
            # Spent the whole budget queueing. Probing now would double it, so
            # answer with the most recent verdict instead — WITHOUT refreshing
            # its deadline, or one burst of congestion would keep a stale
            # conclusion alive indefinitely.
            stale = _health_cache
            if stale is not None:
                return _health_body(stale[2], stale[3])
            logger.error("/health: probe lock wait exhausted the budget with no prior result")
            return _health_body(False, "timeout")

        # Re-check against the CURRENT time: whoever held the lock may have just
        # published, and comparing against a pre-queue timestamp would judge
        # that fresh result stale and defeat the single-flight entirely.
        cached = _health_cache
        if cached is not None and time.monotonic() < cached[1]:
            return _health_body(cached[2], cached[3])
        return await _run_health_probe(budget=max(0.0, deadline - time.monotonic()))
    finally:
        if acquired:
            lock.release()


async def _run_health_probe(budget: float = _HEALTH_DB_TIMEOUT_SEC):
    """Probe the database once and publish the result. Caller holds the lock.

    `budget` is what remains of the CALLER's total allowance, not a fresh one —
    see the note in `health`.
    """
    global _health_cache

    # Taken inside the lock so the publish guard below orders by when this probe
    # actually started, not by when its request arrived.
    started = time.monotonic()
    db_ok = False
    db_detail = "unknown"
    try:
        # `get_db_client()` is inside the budget too: it can build a pool, and
        # aiomysql's connect_timeout defaults to None, so a black-holed database
        # would hang here — past the container healthcheck's own timeout, which
        # would replace our reason with docker's "timed out" and reopen the very
        # blind spot this probe closes.
        async def _probe():
            db = await get_db_client()
            await db.probe()

        await asyncio.wait_for(_probe(), timeout=budget)
        db_ok = True
        db_detail = "connected"
    except asyncio.TimeoutError:
        db_detail = "timeout"
        logger.error(f"/health: database probe timed out after {budget:.2f}s")
    except Exception as exc:
        # Exception TYPE only. This endpoint is public and unauthenticated
        # (`/health` is on backend.auth's allowlist, reachable at
        # https://<app domain>/health), and driver messages carry
        # infrastructure detail: pymysql renders connect failures as
        # "Can't connect to MySQL server on '<rds endpoint>'" and auth failures
        # as "Access denied for user '<user>'@'<internal ip>'". Handing that to
        # anyone who curls during an incident — exactly when it is most likely
        # to be probed — is free reconnaissance. The class name still separates
        # "can't connect" from "auth failed" from "pool dead", which is the
        # distinction the on-call actually needs; the full message goes to the
        # log below.
        db_detail = type(exc).__name__
        # Logged HERE, inside the except block. `logger.exception` outside one
        # has no active exception to render — it prints a literal
        # "NoneType: None" and the driver's message, the only place the real
        # cause survives, is lost. That would leave the on-call with strictly
        # less than before this endpoint was made truthful.
        logger.opt(exception=exc).error(f"/health: database probe failed — {exc}")

    # Cached whether it succeeded or FAILED, for the same duration. Caching
    # only successes would leave the amplification wide open in exactly the
    # situation it is dangerous — a database that is down or slow, with every
    # request paying the full timeout. The cost is that recovery shows up up to
    # 5s late, against a 30s probe interval.
    # Only if no newer probe has already published — see the note on the cache.
    current = _health_cache
    if current is None or started >= current[0]:
        _health_cache = (started, time.monotonic() + _HEALTH_CACHE_TTL_SEC, db_ok, db_detail)

    return _health_body(db_ok, db_detail)


def _health_body(db_ok: bool, db_detail: str):
    """Render the response from a probe result, cached or fresh.

    Split out so the cached path and the fresh path cannot drift — the worker
    counters below are read at RESPONSE time, not probe time, so a cache hit
    still reports the worker's current state.
    """
    body = {
        "status": "healthy" if db_ok else "unhealthy",
        "database": db_detail,
    }
    summary_worker = getattr(app.state, "team_summary_worker", None)
    if summary_worker is not None:
        body["team_summary"] = {
            "running": summary_worker.running,
            **summary_worker.last_pass,
        }

    if not db_ok:
        return JSONResponse(status_code=503, content=body)
    return body


@app.get("/healthz")
async def healthz():
    """K8s/Manyfold readiness probe.

    Always available (not behind ENABLE_MANYFOLD_API gate) so the
    platform can probe before any agent runs. Lightweight — does not
    touch the DB; the more thorough /manyfold/diagnostics endpoint
    covers DB / claude / volume checks.
    """
    return {"status": "ok"}


# ─── Manyfold deployment-gated routers (Part 4.10) ───────────────────────
# Registered only when ENABLE_MANYFOLD_API=1. Without the env, /v1/*
# and /manyfold/* endpoints return 404 — local and EC2 deployments
# behave identically to before.

if os.environ.get("ENABLE_MANYFOLD_API", "").strip() in ("1", "true", "yes"):
    from backend.routes.openai_compat import router as openai_compat_router
    from backend.routes.manyfold.agents import router as manyfold_agents_router
    from backend.routes.manyfold.diagnostics import (
        router as manyfold_diagnostics_router,
    )
    from backend.routes.manyfold.files import router as manyfold_files_router
    from backend.routes.manyfold.sync import (
        config_change_webhook_middleware,
        router as manyfold_sync_router,
    )

    app.include_router(openai_compat_router, tags=["ManyfoldOpenAI"])
    app.include_router(manyfold_agents_router, tags=["ManyfoldAgents"])
    app.include_router(manyfold_diagnostics_router, tags=["ManyfoldDiagnostics"])
    app.include_router(manyfold_files_router, tags=["ManyfoldFiles"])
    app.include_router(manyfold_sync_router, tags=["ManyfoldSync"])
    # Registered last → runs outermost (Starlette LIFO), so it observes the
    # final status code and stays transparent for OPTIONS/non-2xx. It only
    # acts on the response side; the webhook itself no-ops without the
    # MANYFOLD_SYNC_WEBHOOK_* env.
    app.middleware("http")(config_change_webhook_middleware)
    logger.info("Manyfold API enabled: /v1/chat/completions + /manyfold/* registered")
else:
    logger.info("Manyfold API disabled (ENABLE_MANYFOLD_API not set)")


# ─── Frontend static files & SPA fallback ────────────────
# Mounted after all API routes so /api/* and /ws/* take priority.

_FRONTEND_DIST = settings.frontend_dist

if _FRONTEND_DIST.is_dir() and (_FRONTEND_DIST / "index.html").exists():
    logger.info(f"Serving frontend from {_FRONTEND_DIST}")

    app.mount("/assets", StaticFiles(directory=_FRONTEND_DIST / "assets"), name="frontend-assets")

    # HEAD / preflight — Manyfold's ApiChatAdapter (openclaw.adapter.ts:175)
    # probes the root with HEAD before issuing the first chat. Without
    # this explicit handler FastAPI returns 405 (the SPA fallback below
    # is GET-only), failing the platform's readiness check.
    @app.head("/")
    async def preflight_head():
        from fastapi.responses import Response

        return Response(status_code=200)

    # Cache policy: index.html and the SPA fallback MUST NOT be cached
    # by the browser — they hold the immutable-hashed bundle name
    # (``index-XXXXXXXX.js``) and a stale cached copy keeps users on
    # an outdated bundle even after we ship new frontend code (which is
    # exactly what bit us during fragment-auth dev). Hashed asset files
    # under /assets/* are immutable by Vite's design, so they're safe
    # to cache aggressively — that's what _no_cache_headers leaves
    # alone.
    _NO_CACHE_HEADERS = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
    }

    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"])
    async def spa_fallback(request: Request, full_path: str):
        """SPA fallback: return index.html for non-API/WS requests.
        HEAD support is required by Manyfold preflight (see HEAD / above)
        and is cheap to add for arbitrary paths.

        Manyfold-namespace guard: when the Manyfold routers are NOT
        registered (ENABLE_MANYFOLD_API=0), unmatched /v1/* and
        /manyfold/* requests must return 404 — never the SPA bundle.
        Otherwise platform readiness probes get a fake 200.
        """
        if full_path.startswith("v1/") or full_path.startswith("manyfold/"):
            from fastapi.responses import JSONResponse

            return JSONResponse(status_code=404, content={"detail": "not found"})
        file_path = _FRONTEND_DIST / full_path
        if full_path and file_path.is_file():
            # /assets/index-<hash>.js etc — Vite hashes these so they're
            # safe to long-cache. Don't add no-cache headers.
            return FileResponse(file_path)
        # The HTML shell — must always be fresh so the user picks up
        # new bundle names after we ship.
        return FileResponse(
            _FRONTEND_DIST / "index.html",
            headers=_NO_CACHE_HEADERS,
        )
else:
    logger.info("Frontend dist not found, API-only mode")

    @app.get("/")
    async def root():
        """Health check endpoint (no frontend)"""
        return {
            "status": "ok",
            "service": "Agent Context API",
            "version": "1.0.0",
        }


if __name__ == "__main__":
    import uvicorn

    # ws_ping_interval / ws_ping_timeout override uvicorn's 20s/20s defaults
    # that were hanging WS streams on long LLM turns — see BUG_FIX_LOG Bug 32.
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        ws_ping_interval=30,
        ws_ping_timeout=60,
    )
