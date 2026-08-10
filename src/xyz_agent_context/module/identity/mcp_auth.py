"""
@file_name: mcp_auth.py
@author:
@date: 2026-08-10
@description: Server-side identity AUTH for module MCP servers (blueprint P1).

``module/_mcp_identity.py`` answers "who does the caller SAY they are" — a
deliberately fail-open convenience. This module answers "can they PROVE it":
Ed25519 verification of the identity token the platform stamped into the same
header channel (bearer field #7 / X-NarraNexus-Identity-Token), plus the
owner-scoped access policy built on that proof.

Gated by ``NX_MCP_AUTH_MODE``:
  off     (default) — nothing changes anywhere; a local ``bash run.sh`` stays
          byte-identical (iron rule #7).
  audit   — verify + log; NEVER rejects. The measurement phase: its logs and
          audit rows decide when enforce is safe (which callers still arrive
          tokenless).
  enforce — tool-call POSTs without a valid token are 401'd at the door, and
          OwnerScopedPolicy denies cross-owner agent access at the tool layer.

Scope of the door check: POSTs only. Tool calls are POSTs on both transports
(/messages/ on SSE, /mcp on streamable HTTP); GETs are connection handshakes
and stay open, as does /health (compose probes carry no identity).

Missing public key under enforce fails OPEN with a loud warning: mcp is the
data plane — a deploy misconfiguration must degrade to audit semantics, not
take every agent's tools down.
"""
from __future__ import annotations

import json
import os
from contextvars import ContextVar
from typing import Any, Optional

from loguru import logger

from xyz_agent_context.module.identity.tokens import (
    VerifiedIdentity,
    load_public_key_pem,
)

AUTH_MODE_ENV = "NX_MCP_AUTH_MODE"
_MODES = ("off", "audit", "enforce")

# Paths that stay open in every mode: health probes carry no identity.
_EXEMPT_PATHS = frozenset({"/health", "/healthz"})

# The verified caller of the CURRENT request. Set by IdentityAuthMiddleware,
# read by the ownership policy below (and any tool that wants the proven
# identity). None = no proof presented / verification failed / auth off.
_verified_var: ContextVar[Optional[VerifiedIdentity]] = ContextVar(
    "nx_mcp_verified_identity", default=None
)


def auth_mode() -> str:
    """The configured mode. An unknown value reads as *audit*, not off — a
    typo'd mode must surface in logs rather than silently disable auth."""
    raw = (os.environ.get(AUTH_MODE_ENV) or "off").strip().lower()
    if raw in _MODES:
        return raw
    logger.warning(f"[mcp-auth] unknown {AUTH_MODE_ENV}={raw!r}; treating as audit")
    return "audit"


def verified_caller() -> Optional[VerifiedIdentity]:
    """The cryptographically proven caller of the current request, or None."""
    return _verified_var.get()


def verified_caller_for_tool_call() -> Optional[VerifiedIdentity]:
    """The proven caller of the CURRENT TOOL CALL — per-message, not
    per-connection.

    Why this exists (pipeline review of PR #260, verified against mcp 1.24
    sources): the middleware's ContextVar is a snapshot of the request that
    STARTED the transport session — on SSE the tool handler runs inside the
    GET /sse task, on stateful streamable HTTP inside the long-lived
    initialize-time session task. The self-declared facts (`agent_id`,
    `user_id`) are read per-message via `request_ctx` (explicitly threaded
    through ServerMessageMetadata), so the PROOF must come from the same
    source or an adapter that only sets Authorization on tool-call POSTs
    would pass the door yet leave the ownership policy blind.

    Precedence: ambient (per-message) headers when a request is in scope —
    and their verdict is FINAL, even when it is "no proof" (falling back to
    the connection snapshot there would resurrect the mismatch this fixes).
    The ContextVar is only the fallback when there is no ambient MCP request
    at all (direct calls, unit tests).
    """
    from xyz_agent_context.module._mcp_identity import _ambient_headers

    headers = _ambient_headers()
    if headers is None:
        return verified_caller()
    public_key = load_public_key_pem()
    if public_key is None:
        return None
    from xyz_agent_context.module.identity.verify import verify_caller_identity

    identity, _reason = verify_caller_identity(headers, public_key)
    return identity


# ---------------------------------------------------------------------------
# Tokenless measurement — audit mode's entire purpose
# ---------------------------------------------------------------------------
#
# A tool-call POST arriving with NO token is the one fact the audit window
# exists to count (which callers must be onboarded before enforce can flip).
# Logging each one would flood; logging none reads as "everyone has a token"
# even when NOBODY does (incident lesson #4: no signal is not a signal).
# So: aggregate per (method, path), flush one WARNING line + one sampled
# instance_executor_audit row per window — the flip decision reads SQL, not
# grep (incident lesson #5). Enforce mode needs none of this: its tokenless
# POSTs are individually rejected and logged at the door.
_TOKENLESS_FLUSH_SECONDS = 60.0
_tokenless_counts: dict[tuple[str, str], int] = {}
_tokenless_flush_deadline: float = 0.0


async def _note_tokenless(method: str, path: str) -> None:
    global _tokenless_flush_deadline
    import time

    key = (method, path)
    _tokenless_counts[key] = _tokenless_counts.get(key, 0) + 1
    now = time.monotonic()
    if now < _tokenless_flush_deadline:
        return
    _tokenless_flush_deadline = now + _TOKENLESS_FLUSH_SECONDS
    counts = dict(_tokenless_counts)
    _tokenless_counts.clear()
    total = sum(counts.values())
    detail = {f"{m} {p}": n for (m, p), n in sorted(counts.items())}
    logger.warning(
        f"[mcp-auth] audit: {total} tokenless tool-call POST(s) this window — "
        + ", ".join(f"{k} ×{n}" for k, n in detail.items())
    )
    try:
        from xyz_agent_context.repository.executor_audit_repository import (
            ExecutorAuditRepository,
        )
        from xyz_agent_context.schema.executor_audit import EVENT_MCP_AUTH_TOKENLESS
        from xyz_agent_context.utils.db.db_factory import get_db_client

        db = await get_db_client()
        await ExecutorAuditRepository(db).record(
            event_type=EVENT_MCP_AUTH_TOKENLESS,
            detail={"total": total, "counts": detail},
        )
    except Exception as e:  # noqa: BLE001 — the observer must not break the observed
        logger.debug(f"[mcp-auth] tokenless audit row not written: {e}")


class IdentityAuthMiddleware:
    """Pure ASGI middleware — one instance wraps each module server's app.

    Kept ASGI-level (not Starlette BaseHTTPMiddleware) so it composes with the
    streamable-HTTP transport's custom lifespan and never re-buffers SSE
    streams.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        mode = auth_mode()
        if mode == "off":
            await self.app(scope, receive, send)
            return

        public_key = load_public_key_pem()
        if public_key is None:
            # Whole-middleware degradation, BEFORE looking at any token: with
            # no key provisioned the broker cannot have signed one either, so
            # every request arrives tokenless and enforce would take the whole
            # data plane down over a deploy misconfiguration. Fail OPEN, loudly.
            if mode == "enforce":
                logger.warning(
                    "[mcp-auth] enforce requested but no identity public key is "
                    "provisioned — FAILING OPEN (audit semantics). Fix the "
                    "deploy: mount the key and set NX_IDENTITY_PUBLIC_KEY_FILE."
                )
            await self.app(scope, receive, send)
            return

        headers = _ScopeHeaders(scope)
        from xyz_agent_context.module.identity.verify import verify_caller_identity

        identity, reason = verify_caller_identity(headers, public_key)
        path = scope.get("path", "")
        method = scope.get("method", "")

        if identity is None and reason != "no-token":
            # Bad proof — log in every mode (audit's whole job).
            logger.warning(f"[mcp-auth] {mode}: {reason} method={method} path={path}")
        elif (
            mode == "audit"
            and identity is None
            and method == "POST"
            and path not in _EXEMPT_PATHS
        ):
            # reason == "no-token": the measurement the audit window exists
            # for. Aggregated — see _note_tokenless.
            await _note_tokenless(method, path)

        if (
            mode == "enforce"
            and identity is None
            and method == "POST"
            and path not in _EXEMPT_PATHS
        ):
            if reason == "no-token":
                logger.warning(f"[mcp-auth] enforce: no token method={method} path={path}")
            await _reject(send, reason)
            return

        token = _verified_var.set(identity)
        try:
            await self.app(scope, receive, send)
        finally:
            _verified_var.reset(token)


class _ScopeHeaders:
    """Minimal case-insensitive header lookup over a raw ASGI scope."""

    def __init__(self, scope) -> None:
        self._items = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }

    def get(self, key: str, default=None):
        return self._items.get(key.lower(), default)


async def _reject(send, reason: str) -> None:
    body = json.dumps(
        {
            "error": "identity required",
            "detail": (
                "This MCP server requires a platform-signed identity token. "
                f"Verification said: {reason}"
            ),
        }
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


# ---------------------------------------------------------------------------
# OwnerScopedPolicy (blueprint P1 authorization layer)
# ---------------------------------------------------------------------------

# agent_id -> (owner, monotonic deadline). An agent's owner (agents.created_by)
# never changes in practice, but a short TTL keeps this self-correcting rather
# than a second source of truth — the point is only to keep the hot tool-call
# path from adding one MySQL point-read per call.
_OWNER_CACHE_TTL_SECONDS = 60.0
_OWNER_CACHE_MAX = 4096
_owner_cache: dict[str, tuple[str, float]] = {}


async def _resolve_owner_cached(db, agent_id: str) -> str:
    import time

    now = time.monotonic()
    cached = _owner_cache.get(agent_id)
    if cached is not None and cached[1] > now:
        return cached[0]
    from xyz_agent_context.repository import AgentRepository

    owner = await AgentRepository(db).resolve_owner(agent_id)
    # Cache POSITIVE resolutions only. resolve_owner returns "" for three
    # very different things — empty id, unknown agent, AND a failed db query
    # — and "" makes the policy fail open, so caching it would pin an
    # "unknown → allow" verdict for 60s off one MySQL hiccup, invisibly (no
    # owner = no mcp_auth_denied row either). Unknown agents re-query every
    # call, which is fine: they are never the hot path.
    if owner:
        if len(_owner_cache) >= _OWNER_CACHE_MAX:
            # Bounded: drop expired entries; if everything is somehow live,
            # reset — correctness never depends on this cache.
            expired = [k for k, (_, dl) in _owner_cache.items() if dl <= now]
            for k in expired:
                del _owner_cache[k]
            if len(_owner_cache) >= _OWNER_CACHE_MAX:
                _owner_cache.clear()
        _owner_cache[agent_id] = (owner, now + _OWNER_CACHE_TTL_SECONDS)
    return owner


async def check_agent_ownership(agent_id: Any) -> Optional[str]:
    """The verified caller must OWN the target agent. None = allow; an error
    string = deny (enforce only).

    No verified identity / local mode / unknown agent → allow: this policy can
    only ever TIGHTEN a proven identity, never break the fail-open baseline.
    Denials are recorded to instance_executor_audit in audit AND enforce mode
    (incident lesson #5 — the DB row is the measurement that gates the
    audit→enforce flip).
    """
    # Cheapest gates first: with the default (off) / local mode, a tool call
    # must cost literally nothing extra here — verified_caller_for_tool_call
    # does a stat() + an Ed25519 verify and runs only once these pass.
    if not isinstance(agent_id, str) or not agent_id:
        return None
    from xyz_agent_context.utils.deployment_mode import is_cloud_mode

    if not is_cloud_mode():
        return None
    mode = auth_mode()
    if mode == "off":
        return None
    ident = verified_caller_for_tool_call()
    if ident is None:
        return None
    from xyz_agent_context.repository.executor_audit_repository import (
        ExecutorAuditRepository,
    )
    from xyz_agent_context.schema.executor_audit import EVENT_MCP_AUTH_DENIED
    from xyz_agent_context.utils.db.db_factory import get_db_client

    db = await get_db_client()
    owner = await _resolve_owner_cached(db, agent_id)
    if not owner or owner == ident.user_id:
        return None
    logger.warning(
        f"[mcp-auth] {mode}: token user {ident.user_id!r} does not own "
        f"agent {agent_id!r} (owner {owner!r})"
    )
    await ExecutorAuditRepository(db).record(
        event_type=EVENT_MCP_AUTH_DENIED,
        user_id=ident.user_id,
        detail={"agent_id": agent_id, "owner": owner, "mode": mode},
    )
    if mode == "enforce":
        return (
            f"Error: your verified identity ({ident.user_id}) does not own "
            f"agent {agent_id}. You can only operate your own agents."
        )
    return None


__all__ = [
    "AUTH_MODE_ENV",
    "IdentityAuthMiddleware",
    "auth_mode",
    "check_agent_ownership",
    "verified_caller",
    "verified_caller_for_tool_call",
]
