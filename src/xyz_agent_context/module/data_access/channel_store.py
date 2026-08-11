"""
@file_name: channel_store.py
@author:
@date: 2026-08-10
@description: ChannelCredentialStore — the MCP data-access seam for per-channel
send credentials (blueprint P2, #2 "mcp zero db creds", PR-A foundation).

Sibling of ``data_access/store.py``'s AgentDataStore, but a SEPARATE seam
(design decision, 2026-08-10 Owner call): AgentDataStore moves narrative data
(awareness/chat/social/job/memory); this seam returns a RAW per-channel secret
(Discord ``bot_token``, Lark ``app_secret``, …) a send tool needs to
authenticate to the external platform at runtime. Different concern, security-
sensitive shape, own Protocol — same transport/factory pattern (env-gated
DirectStore/HttpStore) as AgentDataStore for consistency, not by folding one
into the other.

- DirectStore: local — dispatches ``channel`` to the matching per-channel
  credential manager (``_MANAGER_FOR``) and returns the manager's dataclass
  serialised via ``to_raw_dict()`` (NOT ``to_public_dict()`` — the whole
  reason this seam exists is to carry the secret across the HTTP hop).
- HttpStore: cloud — GETs the owner-gated backend endpoint
  (``backend/routes/agents/channel_credentials.py``), forwarding the caller
  identity headers the same way AgentDataStore's HttpStore does. Never
  raises: an unreachable backend, a non-2xx, or an unbound credential all
  degrade to ``None`` so a send tool falls through to its existing
  "no_credential" branch instead of crashing the agent's turn.

PR-A wires only ``discord`` into ``_MANAGER_FOR``; each further channel (Slack,
Telegram, WeChat, NarraMessenger, Lark) is its own PR per the migration design
(``reference/self_notebook/specs/2026-08-10-mcp-channel-credential-migration-design.md``),
adding one line to the dict — no seam change.

Known gap (tracked, not hidden): write/lifecycle operations (bind / unbind /
test-connection) are NOT part of this Protocol yet — only the read side
(``get_credential`` / ``get_agent_name``) is migrated in PR-A. ``DirectStore``
exposes an extra ``get_manager()`` convenience (outside the Protocol) so
``_discord_mcp_tools.py``'s bind/status/unbind tools can keep working without
a hand-rolled ``XYZBaseModule.get_mcp_db_client()`` call of their own — but
that convenience has NO HttpStore counterpart, so those three tools still
require local db access even when ``NARRANEXUS_BACKEND_URL`` is set. Removing
``DB_PASSWORD`` from the mcp container (the definition of done for #2) is not
real until a later PR gives bind/unbind their own HTTP-routed seam methods.
"""
from __future__ import annotations

from typing import Any, Optional, Protocol
from urllib.parse import quote

from loguru import logger


def _seg(value: str) -> str:
    """Percent-encode an id used as a URL PATH SEGMENT — see store.py's ``_seg``
    for the full rationale (LLM-supplied ids may contain ``?``/``#``/``..``)."""
    return quote(str(value), safe="")


class ChannelCredentialStore(Protocol):
    """Read-only per-channel credential access a send tool needs, transport-
    agnostic. Returns the RAW credential dict (including the secret) or None
    when the channel is unbound for that agent."""

    async def get_credential(self, channel: str, agent_id: str) -> Optional[dict]: ...

    async def get_agent_name(self, agent_id: str) -> str: ...

    async def get_agent_owner(self, agent_id: str) -> str: ...


# channel -> (module path, manager class name, read-method name). Lazy (import
# by name only when a call actually needs that channel) so this module never
# pulls every channel package in just to define the dispatch table. The read
# method name is NOT uniform across channels (discord/slack/telegram/wechat use
# `get`; lark uses `get_credential`), hence the third field. Adding a channel =
# one line here + a `to_raw_dict()` on its credential dataclass; the seam, the
# backend endpoint (which delegates to DirectStore), and SUPPORTED_CHANNELS all
# follow.
_MANAGER_REGISTRY: dict[str, tuple[str, str, str]] = {
    "discord": (
        "xyz_agent_context.module.discord_module._discord_credential_manager",
        "DiscordCredentialManager",
        "get",
    ),
    "slack": (
        "xyz_agent_context.module.slack_module._slack_credential_manager",
        "SlackCredentialManager",
        "get",
    ),
    "telegram": (
        "xyz_agent_context.module.telegram_module._telegram_credential_manager",
        "TelegramCredentialManager",
        "get",
    ),
    "wechat": (
        "xyz_agent_context.module.wechat_module._wechat_credential_manager",
        "WeChatCredentialManager",
        "get",
    ),
    "narramessenger": (
        "xyz_agent_context.module.narramessenger_module._narramessenger_credential_manager",
        "NarramessengerCredentialManager",
        "get",
    ),
}

# The allowlist the backend endpoint gates on — derived from the registry so it
# can never drift from what DirectStore can actually resolve.
SUPPORTED_CHANNELS = frozenset(_MANAGER_REGISTRY)


def _manager_class(channel: str):
    """Resolve ``channel`` to its credential-manager class, importing lazily."""
    entry = _MANAGER_REGISTRY.get(channel)
    if entry is None:
        raise ValueError(f"unknown channel: {channel!r}")
    module_path, class_name, _read_method = entry
    import importlib

    return getattr(importlib.import_module(module_path), class_name)


def _read_method_name(channel: str) -> str:
    """The manager's read method for ``channel`` (get / get_credential / …)."""
    entry = _MANAGER_REGISTRY.get(channel)
    if entry is None:
        raise ValueError(f"unknown channel: {channel!r}")
    return entry[2]


class DirectStore:
    """Local: dispatch to the per-channel credential manager, byte-for-byte
    the pre-seam db access every channel's MCP tools did directly."""

    async def _db(self):
        # The one MCP db entry point (module/base.py) — same loop-aware
        # factory every other MCP tool (and AgentDataStore's DirectStore)
        # goes through.
        from xyz_agent_context.module.base import XYZBaseModule

        return await XYZBaseModule.get_mcp_db_client()

    async def get_credential(self, channel: str, agent_id: str) -> Optional[dict]:
        mgr = (_manager_class(channel))(await self._db())
        read = getattr(mgr, _read_method_name(channel))
        cred = await read(agent_id)
        return cred.to_raw_dict() if cred is not None else None

    async def get_agent_name(self, agent_id: str) -> str:
        # Same raw lookup lark's `_get_agent_name` uses (agent_name lives on
        # the channel-agnostic `agents` table, not per-credential) — falls
        # back to the id itself when the agent row or its name is missing.
        db = await self._db()
        row = await db.get_one("agents", {"agent_id": agent_id})
        return (row or {}).get("agent_name", "") or agent_id

    async def get_agent_owner(self, agent_id: str) -> str:
        # created_by (the workspace owner's user id) — NarraMessenger's media
        # send + CLI workspace resolution need it. Returns "" when the agent
        # row is missing (callers treat empty as "no owner signal").
        db = await self._db()
        row = await db.get_one("agents", {"agent_id": agent_id})
        return (row or {}).get("created_by", "") or ""

    async def get_manager(self, channel: str):
        """A live per-channel manager for write/lifecycle operations the
        read-only Protocol doesn't cover (see module docstring's "known gap").
        DirectStore-only — there is no HttpStore equivalent."""
        return (_manager_class(channel))(await self._db())


class HttpStore:
    """Cloud: GET the owner-gated backend endpoint, forwarding the caller
    identity (see ``factory.current_identity_headers`` — the same header set
    AgentDataStore's HttpStore forwards). Never raises: every failure mode
    (unreachable, non-2xx, unbound) degrades to ``None`` so the calling tool's
    existing "no_credential" branch handles it, exactly as an unbound local
    agent would.
    """

    def __init__(self, backend_url: str, identity_headers: Optional[dict] = None) -> None:
        self._base = backend_url.rstrip("/")
        self._headers = identity_headers or {}

    async def get_credential(self, channel: str, agent_id: str) -> Optional[dict]:
        path = f"/api/agents/{_seg(agent_id)}/channels/{_seg(channel)}/credential"
        body = await self._get_json(path)
        if body is None:
            return None
        if body.get("bound") is False:
            return None
        return body

    async def get_agent_name(self, agent_id: str) -> str:
        path = f"/api/agents/{_seg(agent_id)}/channels/name"
        body = await self._get_json(path)
        if not body:
            return agent_id
        return body.get("agent_name") or agent_id

    async def get_agent_owner(self, agent_id: str) -> str:
        path = f"/api/agents/{_seg(agent_id)}/channels/owner"
        body = await self._get_json(path)
        if not body:
            return ""
        return body.get("owner_user_id") or ""

    async def _get_json(self, path: str) -> Optional[dict[str, Any]]:
        """One transport + HTTPError boundary, mirroring store.py's HttpStore.
        Returns None on ANY failure (unreachable / non-2xx / non-JSON) — the
        in-band degradation this whole seam exists to guarantee (a send tool
        must never see an exception from a credential lookup)."""
        import httpx

        try:
            async with httpx.AsyncClient(
                base_url=self._base, headers=self._headers, timeout=20.0
            ) as c:
                r = await c.get(path)
        except httpx.HTTPError as e:
            logger.warning(f"[channel-store] backend unreachable GET {path}: {e}")
            return None
        if r.status_code >= 400:
            logger.warning(f"[channel-store] backend rejected GET {path}: {r.status_code}")
            return None
        try:
            return r.json() or {}
        except ValueError:
            logger.warning(f"[channel-store] backend returned non-JSON response for {path}")
            return None
