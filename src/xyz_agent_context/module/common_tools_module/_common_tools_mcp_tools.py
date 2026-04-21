"""
@file_name: _common_tools_mcp_tools.py
@author: Bin Liang
@date: 2026-04-17
@description: MCP server + tool definitions for CommonToolsModule

Tools exposed:
- web_search(queries, max_results_per_query): DuckDuckGo search, multi-query

Stateless — tools take plain arguments, no agent_id / user_id bookkeeping.

Subprocess isolation for web_search (Bug 24, 2026-04-21)
---------------------------------------------------------
Bug 20 (three-layer asyncio timeouts) stopped the 33-hour whole-
container wedge but couldn't reclaim the leaked threads / FDs from
stuck DDGS calls. Over enough leaks, FD table exhaustion kills *every*
MCP tool on the same process (all MCPs share the Python FD table in
local SQLite mode; even in cloud multi-process mode the CommonTools
process itself becomes permanently broken).

This file now spawns a dedicated subprocess per invocation. Timeout →
``SIGKILL`` → OS reclaims every resource the subprocess held. Retries
kick in automatically for timeouts, crashes, or malformed output. The
only way web_search can permanently fail is if DDG / the network is
legitimately unreachable for all K+1 attempts — at which point the
LLM gets a clean error message, not a hang.

Handler-level hard timeout still present
----------------------------------------
Every tool registered here is wrapped with ``with_mcp_timeout`` so no
single tool invocation can wedge the shared MCP container even if the
subprocess layer somehow fails. Defense in depth.
"""

import asyncio
import functools
import json
import sys
from typing import Any, Callable, Awaitable

from loguru import logger
from mcp.server.fastmcp import FastMCP


def with_mcp_timeout(
    seconds: float,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Hard-cap an MCP tool handler's execution time.

    Wraps the handler in ``asyncio.wait_for``. On timeout, returns a
    structured error payload instead of letting the coroutine hang
    forever. The LLM receives "tool timed out" as a normal tool result
    and can pick an alternative, rather than the whole agent loop
    sitting silent until the SDK's idle timer fires.

    Usage:
        @mcp.tool()
        @with_mcp_timeout(45)
        async def my_tool(...) -> dict:
            ...

    Note: this only bounds the *awaiting* coroutine, not any worker
    threads it spawned. For tools that wrap sync network libraries,
    prefer subprocess isolation (see web_search) instead of relying
    on this wrapper alone.
    """

    def _deco(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(fn)
        async def _wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await asyncio.wait_for(fn(*args, **kwargs), timeout=seconds)
            except asyncio.TimeoutError:
                msg = (
                    f"{fn.__name__} timed out after {seconds}s. "
                    "The tool is temporarily unavailable — try a different "
                    "approach or retry later."
                )
                logger.error(f"[MCP timeout] {msg}")
                # Return a string — FastMCP validates tool output against the
                # wrapped function's return annotation, and our MCP tools
                # that need hard timeouts all return str (web_search, future
                # tools following the same pattern). If a tool returns a
                # richer type later, extend this decorator to consult the
                # annotation and format accordingly.
                return f"[tool_error] {msg}"

        return _wrapper

    return _deco


# =============================================================================
# Subprocess-isolated web_search runner
# =============================================================================

# Module path for the runner script. Spawned via `python -m <module>` so we
# don't depend on filesystem paths — works inside Docker, pyinstaller, etc.
_RUNNER_MODULE = "xyz_agent_context.module.common_tools_module._common_tools_impl.web_search_runner"

# Default command to spawn the runner. Module-level so tests can monkeypatch
# with a fake (e.g. `python -c "..."`) to simulate hang / crash / malformed
# output without touching the real runner or the real DDG network.
_RUNNER_CMD: list[str] = [sys.executable, "-m", _RUNNER_MODULE]

# Per-attempt hard cap. If the subprocess's internal asyncio timeouts
# (5 / 15 / 30 s inside web_search.py) all fail to terminate, we SIGKILL
# at this point. 25s gives inner layers (30s overall cap) a chance... wait:
# the inner OVERALL is 30s which is GREATER than 25s. This is intentional —
# when we hit 25s the subprocess is pathologically stuck (primp spinning in
# C, GIL held, whatever) and inner timeouts can't self-fire. We don't want
# to wait 30s+ "just in case" — at 25s the subprocess has earned a SIGKILL.
_SUBPROCESS_TIMEOUT_S: float = 25.0

# K=3 retries → 4 total attempts. Chosen so transient DDG issues (rate
# limits, CDN hiccups) get multiple shots while hard outages fail cleanly.
_MAX_ATTEMPTS: int = 4

# Fixed short backoff between attempts. Exponential buys nothing here —
# the per-attempt timeout is already long; we just want a tiny breather
# so we don't retry mid-rate-limit.
_RETRY_BACKOFF_S: float = 1.0

# Outermost MCP handler timeout. Must cover worst case:
#   _MAX_ATTEMPTS * _SUBPROCESS_TIMEOUT_S + (_MAX_ATTEMPTS-1) * _RETRY_BACKOFF_S
# = 4 * 25 + 3 * 1 = 103s. Add small buffer for process startup / I/O.
_WEB_SEARCH_HANDLER_TIMEOUT_S: float = 110.0


class _RunnerFailure(Exception):
    """Raised when a single subprocess attempt fails in a retry-eligible way."""


async def _spawn_runner(queries: list[str], max_results: int) -> list[dict[str, Any]]:
    """One attempt: spawn the runner subprocess, feed queries, parse output.

    Returns the ``bundles`` list on success. Raises:
      - ``asyncio.TimeoutError`` if the subprocess exceeds
        ``_SUBPROCESS_TIMEOUT_S``. The subprocess is SIGKILL'd before the
        exception propagates so no zombie / leaked FDs survive.
      - ``_RunnerFailure`` if the subprocess exits non-zero or stdout is
        not valid JSON in the expected shape.

    Both exception types signal "retry this".
    """
    payload = json.dumps({
        "queries": queries,
        "max_results_per_query": max_results,
    }).encode("utf-8")

    proc = await asyncio.create_subprocess_exec(
        *_RUNNER_CMD,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=payload),
            timeout=_SUBPROCESS_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        # SIGKILL: Linux unconditionally reaps the process, closing every
        # socket / FD / thread the subprocess held. This is the whole
        # reason subprocess isolation exists — the guarantee we can't get
        # from threads.
        try:
            proc.kill()
            await proc.wait()
        except (ProcessLookupError, OSError):
            pass
        raise

    if proc.returncode != 0:
        raise _RunnerFailure(
            f"runner exited with code {proc.returncode}; "
            f"stderr={stderr.decode('utf-8', errors='replace')[:500]!r}"
        )

    try:
        data = json.loads(stdout.decode("utf-8"))
        bundles = data["bundles"]
        if not isinstance(bundles, list):
            raise TypeError(f"bundles is not a list: {type(bundles).__name__}")
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise _RunnerFailure(
            f"runner stdout malformed: {e}; "
            f"got first 200 bytes={stdout[:200]!r}"
        ) from e

    return bundles


async def _web_search_with_retry(
    queries: list[str],
    max_results: int,
) -> list[dict[str, Any]]:
    """Spawn the runner subprocess with retry-on-failure.

    Retries are triggered by:
      - subprocess timeout (SIGKILL'd, inner resources reclaimed)
      - subprocess crash (non-zero exit)
      - malformed subprocess output (not valid JSON)

    Successful subprocess returns with per-query ``error`` fields in the
    bundles are NOT retried — those are legitimate search failures that
    the LLM can see and act on (e.g. "DDG returned no results for query
    X"). Retrying at the subprocess level won't resolve them.

    Raises ``RuntimeError`` if all ``_MAX_ATTEMPTS`` attempts fail.
    """
    last_error: str = ""
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return await _spawn_runner(queries, max_results)
        except asyncio.TimeoutError:
            last_error = f"subprocess timed out after {_SUBPROCESS_TIMEOUT_S}s (killed)"
            logger.warning(
                f"web_search attempt {attempt}/{_MAX_ATTEMPTS} "
                f"hit subprocess timeout; killed and will retry"
            )
        except _RunnerFailure as e:
            last_error = str(e)
            logger.warning(
                f"web_search attempt {attempt}/{_MAX_ATTEMPTS} failed: {e}"
            )

        if attempt < _MAX_ATTEMPTS:
            await asyncio.sleep(_RETRY_BACKOFF_S)

    raise RuntimeError(
        f"web_search failed after {_MAX_ATTEMPTS} attempts; last error: {last_error}"
    )


# =============================================================================
# MCP server factory
# =============================================================================


def create_common_tools_mcp_server(port: int) -> FastMCP:
    mcp = FastMCP("common_tools_module")
    mcp.settings.port = port

    @mcp.tool()
    @with_mcp_timeout(_WEB_SEARCH_HANDLER_TIMEOUT_S)
    async def web_search(
        queries: list[str],
        max_results_per_query: int = 5,
    ) -> str:
        """Search the web via DuckDuckGo and return the top hits.

        Accepts a **list** of queries and runs them in parallel — pass multiple
        queries when you want to cover different angles in a single round trip.

        Each entry in `queries` can be EITHER:
        - A natural-language question (e.g. "How does Python asyncio gather handle exceptions?")
        - A set of keywords (e.g. "python asyncio gather exception propagation")

        Use whichever form is more likely to match how the information is written
        on the web. For factual lookups, keywords often work better; for
        reasoning/"how/why" questions, full sentences often retrieve better pages.

        Args:
            queries: List of search queries. Empty strings are dropped.
                Recommended: 1–5 queries per call. DuckDuckGo will rate-limit
                aggressive fan-out.
            max_results_per_query: Max hits per query. Default 5, hard cap 10.

        Returns:
            Markdown-formatted results grouped by query. Each hit has title,
            URL, and a short snippet. If a query fails or times out, the
            error is reported inline without breaking the other queries.
        """
        from xyz_agent_context.module.common_tools_module._common_tools_impl.web_search import (
            format_results,
        )

        try:
            bundles = await _web_search_with_retry(queries, max_results_per_query)
        except RuntimeError as e:
            logger.error(f"CommonToolsMCP: web_search gave up: {e}")
            return f"web_search failed: {e}"

        logger.info(
            f"CommonToolsMCP: web_search returned {sum(len(b['results']) for b in bundles)} hits "
            f"across {len(bundles)} queries"
        )
        return format_results(bundles)

    return mcp
