"""
@file_name: web.py
@author: Bin Liang
@date: 2026-09-07
@description: ``narranexus.sdk.web`` — the request-scoped host API a plugin router calls (identity, ownership, CSRF, settings, egress).

A plugin router needs four things the host owns: who is calling, whether
they may touch this agent, the host's deployment settings, and the host's
egress policy. Before this module those came from ``backend.auth``,
``backend.routes._ownership``, ``backend.routes._mcp_egress`` and
``backend.config`` — three of them underscore-private, none of them
importable by a third-party plugin. That made the documented router sample
(``builtin.job``) uncopyable, and left "write your own ownership check" as
the only option for anyone outside this repo — the exact origin of the
2026-08-12 IDOR batch.

These are thin module-level functions rather than a class the plugin holds,
because a router body has no injection point: the seam has to be callable
from inside a handler with nothing but the request. Each call resolves the
one ``WebHost`` implementation the host exposed at boot
(``contracts.services:WEB_HOST``); resolution happens per call, not at
import, so a router module can be imported in a process that has not booted
yet (route collection at import time is how the backend builds its route
table).

``UnknownEntry`` from ``host()`` is the honest failure: it means this
process has no HTTP host, i.e. a router is being invoked from a worker or
MCP role. It is never silently degraded to "allow".
"""
from __future__ import annotations

from typing import Any, Mapping

from narranexus.contracts.services import WEB_HOST
from narranexus.contracts.web import (
    IDENTITY_UNRESOLVED,
    TOKEN_EXPIRED,
    TOKEN_INVALID,
    AuthError,
    HostSettings,
    WebHost,
)


def host() -> WebHost:
    """The process's ``WebHost``. Raises ``UnknownEntry`` when this process is not an HTTP host."""
    from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES

    return KERNEL_REGISTRIES.services.require(WEB_HOST)


async def current_user_id(request: Any) -> str:
    """The authenticated caller on ``request``."""
    return await host().current_user_id(request)


def decode_session_token(token: str) -> Mapping[str, Any]:
    """Verify + decode a host session JWT (``auth`` provider plugins only)."""
    return host().decode_session_token(token)


def auth_error(code: str, detail: str, status_code: int = 401) -> Exception:
    """Build the host's auth rejection — ``raise auth_error(...)``."""
    return host().auth_error(code, detail, status_code)


async def require_agent_owner(request: Any, agent_id: str) -> None:
    """Deny unless the caller owns ``agent_id`` (raises 404/403; 503 when the lookup itself fails)."""
    await host().require_agent_owner(request, agent_id)


async def check_agent_owner(request: Any, agent_id: str) -> str | None:
    """The same decision as an error string (or None) for routes that answer with a payload, not a status."""
    return await host().check_agent_owner(request, agent_id)


async def resolve_viewer(request: Any) -> str:
    """The dashboard viewer identity (rejects a ``user_id`` query param)."""
    return await host().resolve_viewer(request)


async def agent_visible(viewer_id: str, agent_id: str) -> Mapping[str, Any]:
    """The agent row when ``viewer_id`` may see it; 404 otherwise."""
    return await host().agent_visible(viewer_id, agent_id)


def reject_cross_origin(request: Any) -> None:
    """CSRF guard for unauthenticated local-mode writes."""
    host().reject_cross_origin(request)


def host_settings() -> HostSettings:
    """The host's deployment settings (today: the upload ceiling)."""
    return host().settings()


async def filter_public_mcp_servers(mcp_servers: Mapping[str, Any]) -> Mapping[str, Any]:
    """Drop MCP servers the host's SSRF egress check refuses (cloud only, fail-closed)."""
    return await host().filter_public_mcp_servers(mcp_servers)


def artifact_view_token(*, agent_id: str, artifact_id: str) -> str:
    """Mint a view token for the host's public artifact raw route."""
    return host().artifact_view_token(agent_id=agent_id, artifact_id=artifact_id)


__all__ = [
    "IDENTITY_UNRESOLVED",
    "TOKEN_EXPIRED",
    "TOKEN_INVALID",
    "AuthError",
    "HostSettings",
    "WebHost",
    "agent_visible",
    "artifact_view_token",
    "auth_error",
    "check_agent_owner",
    "current_user_id",
    "decode_session_token",
    "filter_public_mcp_servers",
    "host",
    "host_settings",
    "reject_cross_origin",
    "require_agent_owner",
    "resolve_viewer",
]
