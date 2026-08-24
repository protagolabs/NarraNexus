"""
@file_name: executor_service.py
@author:
@date: 2026-06-17
@description: The agent-loop Executor service.

This is the ONLY tier that spawns the claude/codex CLI. It is a thin,
near-stateless FastAPI app: given an assembled prompt + the resolved
(scoped) provider configs + the workspace path, it runs the LOCAL
agent-loop driver and streams the raw event dicts back as NDJSON.

Security shape (why this exists):
  * It holds NO platform master secrets (no JWT/DB/admin keys). Its
    container is started WITHOUT the platform .env; the only credential
    it sees is the per-run scoped LLM key, which arrives in the request
    body and is applied to a ContextVar for the duration of the loop.
  * It needs NO database — the orchestrator did all DB work (steps
    0-2.5) and ships the assembled messages + configs.
  * Because the executor process does NOT set ``AGENT_EXECUTOR_URL``,
    ``get_agent_loop_driver`` here resolves to the LOCAL claude/codex
    driver (no self-recursion).
  * EVERY field of the body describes only THIS request, which is what
    makes leaving the endpoint unauthenticated tolerable. That property
    held once before and was lost: a ``resume_session_id`` named a CLI
    transcript in a ``CLAUDE_CONFIG_DIR`` shared across tenants, so a
    direct caller with a guessed handle could replay someone else's
    conversation — which is why that one capability used to carry a
    per-call HMAC (``EXECUTOR_RESUME_HMAC_SECRET``). Both the field and
    the signature are gone (2026-07-29): the claude adapter writes the
    CLI transcript itself, inside this container, and deletes it when the
    turn ends, so nothing durable exists for a caller to point at.
    Anything added to this body later must preserve the property or
    re-earn it the same way.

Per-agent / per-user workspace isolation is a deployment concern layered
on top (mount only that user's workspace into the container) — out of
scope for this module, which just runs the loop it is handed.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from time import monotonic

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from loguru import logger

# Importing the package registers the built-in agent-loop drivers
# (claude_code / codex_cli / nexus_power) into the registry.
import xyz_agent_context.agent_framework  # noqa: F401
from xyz_agent_context.agent_framework.loop.driver import (
    get_agent_loop_driver,
)
from xyz_agent_context.agent_runtime.executor_protocol import (
    apply_provider_configs,
)

@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Prime warm-runner pools for the frameworks in EXECUTOR_PREWARM_FRAMEWORKS
    # (default: nexus_power) BEFORE serving, so the process's first turn on those
    # frameworks draws a pre-imported runner instead of paying the cold
    # subprocess import inline (measured ~12s cold vs ~2s warm on dev; see
    # NexusAgent.warmup). Gated so ops can drop the per-container ~350MB idle-
    # runner cost on a memory-pressured host by setting the var empty. Cloud
    # executors receive this env ONLY because the broker forwards it (deploy
    # broker.py + compose "EXECUTOR_PREWARM_FRAMEWORKS-nexus_power", single-dash
    # so an explicit empty value survives); local/dev reads it from the process
    # env directly. A follow-up can make the broker pass the container's ACTUAL
    # framework so only the ones a user really uses are primed. Best-effort: a
    # warmup failure only logs and NEVER stops the executor from booting.
    frameworks = [
        f.strip()
        for f in os.getenv("EXECUTOR_PREWARM_FRAMEWORKS", "nexus_power").split(",")
        if f.strip()
    ]
    for name in frameworks:
        try:
            driver = get_agent_loop_driver(name)
            _warmup = getattr(driver, "warmup", None)
            if callable(_warmup):
                _warmup()
            else:
                # Not an error: local/desktop drivers (and remote_driver) may
                # have no warmup; make the skip visible instead of silent.
                logger.debug(f"[Executor] driver for {name!r} has no warmup(); skipped")
        except Exception as e:  # noqa: BLE001 - warmup is best-effort, never fatal
            logger.warning(f"[Executor] {name} warmup skipped: {e}")
    yield


app = FastAPI(title="NarraNexus Agent-Loop Executor", lifespan=_lifespan)


# Long-lived work in flight inside THIS container, reported on /health as
# ``busy`` so the broker can refuse to stop a container that is working.
#
# Why the broker needs it: it only ever observes turn START (ensure() is
# called once per turn; the orchestrator then streams against the container
# directly), so its idle timer measures "time since a turn began". A turn
# outliving that TTL is indistinguishable from an abandoned container — and
# binding rule #14 makes multi-hour turns first-class, so "keep the TTL
# comfortably above the longest turn" is not an available answer.
#
# Why the CONTAINER answers rather than the orchestrator's DB: for "may I
# stop this container" this is the exact question and the exact answer — no
# events-row write window, no dependence on run recording being enabled.
# (Stale IMAGE replacement deliberately keeps using the orchestrator's
# verdict: the container there runs an old image that may predate this
# field, so probing it would answer "cannot tell" forever and the image
# would never roll.)
#
# Plain dict, no lock: every reader and writer runs on the event loop thread
# (the middleware below, and /health — which is `async def` precisely so this
# stays true). Nothing here is safe to read from a worker thread.
# Values are monotonic start times so /health can report the OLDEST in-flight
# request's age. A bare count could not distinguish a legitimate 10-hour turn
# (rule #14 — must keep reporting busy) from something pinned open: a request
# that never ends holds this container busy forever, which the broker now
# honours, so the container stops being reapable. Age makes that state a
# visible fact instead of an assumption.
_inflight_started: dict[int, float] = {}
_next_work_id: int = 0

# Request prefixes that mean "someone is using this container". /agent-loop is
# a streaming turn; the office-watch endpoints proxy to a server running
# INSIDE the container, and reaping under them destroys that session too. A
# prefix list rather than "any request": /health itself must never mark the
# container busy, or the broker's probe would be self-fulfilling.
#
# Known consequence, deliberate: a browser tab left on a document holds its
# watch stream open, so that container reports busy indefinitely and the
# broker will not reclaim its slot. The alternative is the status quo — the
# reaper destroys the watch session out from under the user at the idle TTL —
# and between "a slot stays held while someone has the document open" and
# "editing silently breaks", the first is the right failure. The real fix is
# for such holders to carry an expiring lease rather than an open socket;
# until then this is a slot-pressure trade, not a correctness one.
_WORK_PATH_PREFIXES = ("/agent-loop", "/watch")

# Budget for reading a request BODY. Generous next to any real payload, and
# unrelated to how long the turn itself may run.
_BODY_READ_TIMEOUT_S = 60.0

# Read-gap budget for the office-watch passthrough. Applies ONLY there: the
# agent-loop stream must stay unbounded (rule #14 — a tool call can think for
# hours with nothing to send).
_WATCH_READ_TIMEOUT_S = 300.0


def _is_work_path(path: str) -> bool:
    """Whether this request counts as someone using the container.

    Segment-aware, not a bare prefix: ``startswith`` would silently enrol a
    future ``/watchdog`` or ``/agent-loop-metrics``, and a high-frequency one
    would pin the container busy forever — with nothing connecting the
    symptom (never reclaimed) to the cause (a new route's name).
    """
    return any(
        path == prefix or path.startswith(prefix + "/")
        for prefix in _WORK_PATH_PREFIXES
    )


class InFlightWorkMiddleware:
    """Count in-flight work as raw ASGI, NOT inside the route handler.

    The handler cannot do this correctly. ``StreamingResponse.stream_response``
    sends ``http.response.start`` BEFORE touching ``body_iterator``, so when
    the consumer is already gone the generator is never started — and closing
    a never-started async generator runs none of its code, ``finally``
    included. The counter would then never come back down and the container
    would report ``busy`` for the rest of its life: never reapable, no
    self-heal, invisible to every metric.

    At this layer the accounting brackets ``await self.app(...)``, which every
    exit path passes through — normal completion, client disconnect,
    cancellation, an exception raised before streaming ever begins.

    Raw ASGI rather than ``@app.middleware("http")`` on purpose: that
    decorator is ``BaseHTTPMiddleware``, which re-wraps streaming bodies
    through an anyio memory stream — an extra copy of every NDJSON frame, on
    the one path whose frames reach hundreds of KiB.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http" or not _is_work_path(
            str(scope.get("path", ""))
        ):
            await self.app(scope, receive, send)
            return
        global _next_work_id
        _next_work_id += 1
        work_id = _next_work_id
        _inflight_started[work_id] = monotonic()
        try:
            await self.app(scope, receive, send)
        finally:
            _inflight_started.pop(work_id, None)


app.add_middleware(InFlightWorkMiddleware)


@app.get("/health")
async def health() -> dict:
    """Liveness plus "am I working right now?".

    ``busy`` is what stops the broker's idle reaper from stopping a container
    mid-turn. ``inflight_oldest_s`` is here so an in-flight count that never
    drains is diagnosable rather than merely mysterious.

    ``async def`` is load-bearing, not style. FastAPI runs a SYNC handler in a
    worker thread, which would put these reads on a different thread from the
    middleware's writes: ``min()`` iterates, and a dict resized mid-iteration
    raises RuntimeError, while the three reads below could also land on
    either side of an insert and report ``busy`` with no age. On the loop
    thread none of that can interleave.
    """
    oldest = min(_inflight_started.values(), default=None)
    return {
        "status": "healthy",
        "busy": bool(_inflight_started),
        "inflight_work": len(_inflight_started),
        "inflight_oldest_s": (
            None if oldest is None else round(monotonic() - oldest, 1)
        ),
    }


@app.post("/watch/ensure")
async def watch_ensure(request: Request) -> JSONResponse:
    """Start (or reuse) an `officecli watch` server INSIDE this container and
    return the port ALLOCATED to the file.

    In cloud the workspace + the agent's officecli edits live in this executor
    container, so the watch MUST run here (the orchestrator can't spawn a
    resident-sharing process into it). The orchestrator's `/office-watch/open`
    calls this before minting the proxy URL, then proxies to the returned port.
    This container owns port allocation (one dedicated port per file), so the
    port is decided here — the orchestrator never guesses it. Reuses a running
    watch if this file already has one.

    Body: {agent_id, user_id, file}. No auth (internal-trust, same as
    /agent-loop); the port allowlist + workspace confinement in ensure_watch are
    the guard.
    """
    import asyncio

    from xyz_agent_context.utils.office_watch import ensure_watch

    body = await request.json()
    port = await asyncio.get_running_loop().run_in_executor(
        None,
        ensure_watch,
        body["agent_id"],
        body["user_id"],
        body["file"],
    )
    if port is None:
        return JSONResponse({"ok": False}, status_code=503)
    return JSONResponse({"ok": True, "port": port}, status_code=200)


@app.get("/watch/version")
async def watch_version(request: Request) -> JSONResponse:
    """Return the mtime+size of an office file INSIDE this container.

    The orchestrator's `/office-watch/version` (cloud branch) calls this so the
    frontend's mtime-poll fallback works when the workspace lives in the
    container. Query: agent_id, user_id, file. No auth (internal-trust).
    """
    from xyz_agent_context.utils.office_watch import resolve_watch_file
    from xyz_agent_context.utils.workspace_paths import resolve_existing_workspace

    agent_id = request.query_params.get("agent_id", "")
    user_id = request.query_params.get("user_id", "")
    file = request.query_params.get("file", "")
    try:
        rel = resolve_watch_file(agent_id, user_id, file)
        abs_path = resolve_existing_workspace(agent_id, user_id) / rel
        st = os.stat(abs_path)
    except (ValueError, OSError) as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    return JSONResponse({"mtime": st.st_mtime, "size": st.st_size}, status_code=200)


@app.get("/watch/{port}/{path:path}")
async def watch_passthrough(port: int, path: str, request: Request) -> Response:
    """Reverse-proxy to an `officecli watch` server running INSIDE this
    container (bound to 127.0.0.1:{port}).

    This is the only bridge from the orchestrator into the watch port: the
    broker exposes only this executor's API port, not arbitrary container
    ports. The orchestrator's own /api/office-watch-proxy route forwards to
    here; the browser never reaches this directly.

    No auth (internal-trust, same as /agent-loop — the container holds no
    secrets), but the port allowlist is still enforced as defense-in-depth so
    this can't be turned into an SSRF into other in-container ports.
    """
    import aiohttp

    from xyz_agent_context.utils.office_watch import is_watch_port

    if not is_watch_port(port):
        return JSONResponse({"error": f"port {port} not allowed"}, status_code=403)

    upstream = f"http://127.0.0.1:{port}/{path}"
    if request.url.query:
        upstream += f"?{request.url.query}"
    fwd = {k: v for k, v in request.headers.items() if k.lower() in ("accept", "cache-control", "last-event-id")}
    # sock_read is bounded, unlike the agent-loop stream's. Upstream here is a
    # LOCAL process on 127.0.0.1, so a long read gap is a wedge, not network
    # weather — and an unbounded one pins this request open forever when the
    # client half-closes (laptop sleeps, NAT drops the mapping: no FIN, so no
    # http.disconnect ever arrives). That would hold the container marked busy
    # for good, and the broker now honours busy.
    #
    # The trade-off, with the part that IS knowable pinned down: `officecli`
    # is a prebuilt third-party binary (iOfficeAI/OfficeCLI, pinned by
    # OFFICECLI_VERSION in the deploy repo's Dockerfile.executor), so its
    # keep-alive cadence is not a fact this repo owns — which is exactly why
    # the budget must not depend on it. If it emits nothing for this long, an
    # idle document's stream ends and the browser's EventSource reconnects on
    # its own (the orchestrator proxy's shim keeps that path working); the
    # window is deliberately far longer than any interactive edit gap. The
    # other side of the trade is a container left unreapable for the rest of
    # its life, which no reconnect recovers from.
    timeout = aiohttp.ClientTimeout(
        total=None, sock_read=_WATCH_READ_TIMEOUT_S, sock_connect=10,
    )
    session = aiohttp.ClientSession(timeout=timeout)
    try:
        resp = await session.get(upstream, headers=fwd)
    except aiohttp.ClientError as e:
        await session.close()
        logger.warning(f"[Executor] watch passthrough upstream failed ({upstream}): {e}")
        return JSONResponse({"error": "watch server unavailable"}, status_code=502)

    async def _body():
        try:
            async for chunk in resp.content.iter_any():
                yield chunk
        finally:
            resp.release()
            await session.close()

    media_type = resp.headers.get("Content-Type", "application/octet-stream")
    return StreamingResponse(
        _body(),
        status_code=resp.status,
        media_type=media_type,
        headers={"X-Accel-Buffering": "no"},
    )


@app.post("/agent-loop")
async def agent_loop(request: Request) -> Response:
    """Run one agent turn and stream raw event dicts back as NDJSON.

    One JSON object per line:
      {"event": {...}}            a raw agent-loop event
      {"error": {"type","message"}}  the loop raised
    """
    # Bound the BODY read, and only the body read. This endpoint is
    # unauthenticated and reachable from inside this very container — the
    # agent's own Bash can POST a chunked request and never close it, and
    # request.json() would then wait forever while the middleware above holds
    # the container marked busy, i.e. never reapable. A parse budget, NOT a
    # ceiling on the turn: once the body is in, the loop below runs as long as
    # it likes (rule #14).
    #
    # It closes the single-request wedge, not the general case: a caller
    # inside the container can simply POST again, holding busy for another
    # window each time. That is not a new capability (the same agent can hold
    # the container by running a real turn), and the real boundary is the
    # expiring lease noted above _WORK_PATH_PREFIXES.
    try:
        body = await asyncio.wait_for(request.json(), timeout=_BODY_READ_TIMEOUT_S)
    except TimeoutError:
        # asyncio.CancelledError is a BaseException and still propagates.
        logger.warning("[Executor] agent-loop request body never completed")
        return JSONResponse({"error": "request body timed out"}, status_code=408)
    framework = body["framework"]
    working_path = body["working_path"]

    # Re-apply the orchestrator's resolved (scoped) provider configs onto
    # THIS task's ContextVars, so the CLI authenticates with the right key.
    apply_provider_configs(body.get("provider_configs") or {})


    # AGENT_EXECUTOR_URL is unset in the executor container → local driver.
    driver = get_agent_loop_driver(framework, working_path=working_path)

    async def _stream():
        logger.info(f"[Executor] agent-loop start framework={framework!r} workspace={working_path}")
        try:
            async for event in driver.agent_loop(
                messages=body["messages"],
                mcp_servers=body.get("mcp_servers") or {},
                streaming=bool(body.get("streaming", True)),
                extra_env=body.get("extra_env") or None,
                cancellation=None,  # cancellation = orchestrator aborts the stream
                disallowed_tools=body.get("disallowed_tools") or None,
                agent_id=str(body.get("agent_id") or "agent"),
                expressive_tools=body.get("expressive_tools") or None,
                turn_profile=body.get("turn_profile") or None,
                extra_accessible_roots=body.get("extra_accessible_roots") or None,
                origin_declaration=body.get("origin_declaration") or "",
            ):
                yield json.dumps({"event": event}, default=str) + "\n"
        except Exception as e:  # noqa: BLE001 — surface to caller, never crash the service
            logger.exception(f"[Executor] agent-loop failed: {e}")
            yield json.dumps({"error": {"type": type(e).__name__, "message": str(e)}}) + "\n"

    return StreamingResponse(_stream(), media_type="application/x-ndjson")


def _resolve_executor_log_dir(base_working_path: str) -> Path:
    """Where the executor writes its logs: under the (single) mounted user
    workspace dir, so each user's executor logs land in THEIR directory and
    persist on the host volume — not in a shared sink.

    The broker mounts exactly one user subtree at
    ``{base}/{user_id}``, so ``base`` has a single non-hidden subdir = the
    user. Falls back to ``{base}/.executor_logs`` if it can't be uniquely
    determined (so logging never hard-fails).
    """
    base = Path(base_working_path)
    try:
        subdirs = [p for p in base.iterdir() if p.is_dir() and not p.name.startswith(".")]
    except OSError:
        subdirs = []
    user_dir = subdirs[0] if len(subdirs) == 1 else base
    return user_dir / ".executor_logs"


def main() -> None:
    import os

    import uvicorn

    base = os.environ.get("BASE_WORKING_PATH", "/opt/narranexus/workspaces")
    log_dir = _resolve_executor_log_dir(base)
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        logger.add(
            str(log_dir / "executor_{time:YYYY-MM-DD}.log"),
            rotation="50 MB",
            retention="14 days",
            enqueue=True,
        )
        logger.info(f"[Executor] file logging at {log_dir}")
    except OSError as e:  # noqa: BLE001 — file logging is best-effort
        logger.warning(f"[Executor] could not set up file logging at {log_dir}: {e}")

    port = int(os.environ.get("AGENT_EXECUTOR_PORT", "8020"))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
