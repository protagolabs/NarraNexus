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
  credential manager (``_MANAGER_REGISTRY``) and returns the manager's dataclass
  serialised via ``to_raw_dict()`` (NOT ``to_public_dict()`` — the whole
  reason this seam exists is to carry the secret across the HTTP hop).
- HttpStore: cloud — calls the owner-gated backend endpoints
  (``backend/routes/agents/channel_credentials.py`` for reads, the per-channel
  ``/api/<channel>/<op>`` routes for writes), forwarding the caller identity
  headers the same way AgentDataStore's HttpStore does. Never raises, but the
  degradation shape differs by call kind: a READ (GET) failure — unreachable,
  non-2xx, unbound, non-JSON — degrades to ``None`` so a send tool falls to its
  "no_credential" branch; a WRITE (POST bind/unbind/test) failure degrades to
  ``{success: False, error}`` (a failed unbind is NOT "unbound").

All 7 channels are wired here, but across a few per-channel tables, so adding a
channel is NOT literally one line: reads go through ``_MANAGER_REGISTRY``
(manager class + read-method name); the clean write-leg adds ``_BIND_SERVICE``
(bind's do_bind module + mgr/db shape) for channels with a bind tool and
``_DISPLAY_NAME`` (unbind wording) for channels with an unbind tool; plus a
``to_raw_dict()`` on the dataclass. A future consolidation of these parallel
dicts into one per-channel descriptor (review 🟢) would make "one line" true
again.

Reads (``get_credential`` / ``get_agent_name`` / ``get_agent_owner``) and the
clean writes (``bind`` / ``unbind`` / ``test_connection``, which map to existing
owner-gated backend routes) are all Protocol methods with a DirectStore and an
HttpStore side.

Known gap (tracked, not hidden): lark's CLI-OAuth write flow (setup / the
three-click permission advance / enable-receive) and narramessenger's
``narra-cli`` passthrough are NOT migrated — they spawn a CLI subprocess and, in
lark's case, do partial ``permission_state`` patches with no backend route, so
they still reach the db directly via their own credential manager. Removing
``DB_PASSWORD`` from the mcp container (the definition of done for #2) is not
real until that CLI-write leg gets a generic credential-upsert endpoint. That is
deliberately deferred (Owner is deciding the approach).
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
    """Per-channel credential access a send/bind tool needs, transport-agnostic.

    Reads (get_credential/get_agent_name/get_agent_owner) return the RAW
    credential dict (including the secret) or None when unbound; the clean
    writes (bind/unbind/test_connection) return the backend route's
    ``{success, ...}`` envelope. lark's CLI-OAuth writes are NOT here (deferred —
    see the module docstring's "Known gap")."""

    async def get_credential(self, channel: str, agent_id: str) -> Optional[dict]: ...

    async def get_agent_name(self, agent_id: str) -> str: ...

    async def get_agent_owner(self, agent_id: str) -> str: ...

    async def bind(self, channel: str, agent_id: str, fields: dict) -> dict: ...

    async def unbind(self, channel: str, agent_id: str) -> dict: ...

    async def test_connection(self, channel: str, agent_id: str) -> dict: ...


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
    "lark": (
        "xyz_agent_context.module.lark_module._lark_credential_manager",
        "LarkCredentialManager",
        "get_credential",
    ),
    # Home Assistant has no bot credential — a JSON config blob (base_url + LLAT).
    # A thin repository-backed adapter gives it the seam's uniform read shape.
    "home_assistant": (
        "xyz_agent_context.module.home_assistant_module._home_assistant_impl.binding",
        "HomeAssistantCredentialManager",
        "get",
    ),
}

# The allowlist the backend endpoint gates on — derived from the registry so it
# can never drift from what DirectStore can actually resolve.
SUPPORTED_CHANNELS = frozenset(_MANAGER_REGISTRY)

# Write dispatch for bind (blueprint P2 write leg). unbind is uniform across all
# channels (mgr.unbind(agent_id)) so it needs no per-channel entry; bind is NOT
# — each channel's do_bind lives in its own service module and takes either the
# manager (discord/slack/telegram) or the raw db (narramessenger's gateway-bind).
# ``bind_takes`` records which. Lark is intentionally absent — its bind is part of
# the deferred CLI-OAuth write leg. Channels with no bind MCP tool (wechat: QR)
# simply never call bind() and need no entry.
# Display names the backend `/unbind` routes use in their not-found message
# ("no <Name> credential bound for this agent") — so DirectStore.unbind is
# byte-identical to the HTTP route (the whole point of this seam), not just
# "close". A plain channel.title() would give "Wechat", not "WeChat".
# ONLY the channels whose unbind is actually reachable through this seam need an
# entry: discord/slack/telegram/wechat (their `<ch>_unbind` MCP tool calls
# seam.unbind). narramessenger has no unbind tool, and lark's unbind uses
# delete_credential directly (CLI-write leg, excluded), so neither routes here;
# any channel absent from this map just falls back to the raw channel string.
_DISPLAY_NAME: dict[str, str] = {
    "discord": "Discord",
    "slack": "Slack",
    "telegram": "Telegram",
    "wechat": "WeChat",
}

_BIND_SERVICE: dict[str, tuple[str, str, str]] = {
    "discord": ("xyz_agent_context.module.discord_module._discord_service", "do_bind", "mgr"),
    "slack": ("xyz_agent_context.module.slack_module._slack_service", "do_bind", "mgr"),
    "telegram": ("xyz_agent_context.module.telegram_module._telegram_service", "do_bind", "mgr"),
    "narramessenger": ("xyz_agent_context.module.narramessenger_module._narramessenger_service", "do_bind", "db"),
}


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

    async def bind(self, channel: str, agent_id: str, fields: dict) -> dict:
        """Bind via the channel's own do_bind service (byte-identical to what the
        MCP tool did locally). do_bind takes either the manager or the raw db —
        `_BIND_SERVICE` records which. Returns do_bind's {success, error?, data?}."""
        entry = _BIND_SERVICE.get(channel)
        if entry is None:
            raise ValueError(f"channel {channel!r} has no bind service")
        import importlib

        module_path, fn_name, takes = entry
        do_bind = getattr(importlib.import_module(module_path), fn_name)
        db = await self._db()
        if takes == "mgr":
            return await do_bind((_manager_class(channel))(db), agent_id, **fields)
        return await do_bind(db, agent_id, **fields)

    async def unbind(self, channel: str, agent_id: str) -> dict:
        """mgr.unbind + the nested {"success":True,"data":{"unbound":True}} the
        discord/slack/telegram/wechat `/unbind` routes return, so Direct↔Http are
        parity for the channels that actually expose an unbind MCP tool.

        NOTE for a future narramessenger unbind tool: narra's own `/unbind` route
        returns a FLAT `{"success":True,"unbound":ok}` and reports success even
        when nothing was removed — it does NOT match this envelope. Wire such a
        tool to this seam only after reconciling narra's route to this shape (or
        special-casing narra here); today narra exposes only a bind tool, so the
        mismatch is inert."""
        mgr = (_manager_class(channel))(await self._db())
        removed = await mgr.unbind(agent_id)
        if not removed:
            name = _DISPLAY_NAME.get(channel, channel)
            return {"success": False, "error": f"no {name} credential bound for this agent"}
        return {"success": True, "data": {"unbound": True}}

    async def test_connection(self, channel: str, agent_id: str) -> dict:
        """Re-validate the stored credential against the platform (do_test_connection,
        which lives in the same _service module as do_bind). Read-only — no
        mutation — but it needs the manager, so it routes here rather than staying
        a raw get_mcp_db_client call in the status tool. Only the channels whose
        _service defines do_test_connection AND expose a status tool
        (discord/slack/telegram) call this — a clear error beats a raw
        AttributeError for a channel (e.g. narramessenger) that has neither."""
        import importlib

        # `.get` first: a channel outside _BIND_SERVICE (wechat/lark/
        # home_assistant) must ALSO get the clear ValueError below, not a bare
        # KeyError one line up.
        entry = _BIND_SERVICE.get(channel)
        do_test = (
            getattr(importlib.import_module(entry[0]), "do_test_connection", None)
            if entry is not None
            else None
        )
        if do_test is None:
            raise ValueError(
                f"channel {channel!r} has no test_connection (only discord/slack/telegram wire one)"
            )
        return await do_test((_manager_class(channel))(await self._db()), agent_id)


class HttpStore:
    """Cloud: call the owner-gated backend endpoints, forwarding the caller
    identity (see ``factory.current_identity_headers`` — the same header set
    AgentDataStore's HttpStore forwards). Never raises, but the degradation
    shape differs by call kind:
    - reads (GET credential/name/owner): any failure (unreachable / non-2xx /
      unbound / non-JSON) -> ``None``, so the send tool's existing
      "no_credential" branch handles it exactly as an unbound local agent would.
    - writes (POST bind/unbind/test): any failure -> ``{success: False, error}``
      (a failed unbind is NOT "unbound"), matching the route's own envelope.
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

    async def bind(self, channel: str, agent_id: str, fields: dict) -> dict:
        # POST the channel's owner-gated /bind route (same do_bind the local path
        # runs). The route already accepts the nx-service identity (check_owned
        # reads request.state.user_id, which auth_middleware sets from the bearer).
        # NOTE: the route's Pydantic model also enforces VALUE constraints (e.g.
        # bot_token min_length) that the local do_bind leaves to the platform API,
        # so a MALFORMED field (a truncated token) 422s here where DirectStore
        # would return a friendlier "invalid token" from the API. Both are still
        # {success:False} the tool relays — never-raise holds — the wording just
        # differs for bad input; well-formed input is byte-parity.
        return await self._post_json(f"/api/{_seg(channel)}/bind", {"agent_id": agent_id, **fields})

    async def unbind(self, channel: str, agent_id: str) -> dict:
        return await self._post_json(f"/api/{_seg(channel)}/unbind", {"agent_id": agent_id})

    async def test_connection(self, channel: str, agent_id: str) -> dict:
        return await self._post_json(f"/api/{_seg(channel)}/test", {"agent_id": agent_id})

    async def _post_json(self, path: str, body: dict) -> dict:
        """POST + one HTTPError boundary. Writes must ALWAYS hand back a
        {success, error?} dict the tool can relay — never raise, never None (a
        failed unbind is not "unbound"), so transport failures degrade to
        success:False with a readable reason."""
        import httpx

        try:
            async with httpx.AsyncClient(
                base_url=self._base, headers=self._headers, timeout=20.0
            ) as c:
                r = await c.post(path, json=body)
        except httpx.HTTPError as e:
            logger.warning(f"[channel-store] backend unreachable POST {path}: {e}")
            return {"success": False, "error": f"channel backend unreachable ({type(e).__name__})"}
        if r.status_code >= 400:
            logger.warning(f"[channel-store] backend rejected POST {path}: {r.status_code}")
            return {"success": False, "error": f"channel backend rejected the call ({r.status_code})"}
        try:
            return r.json() or {"success": False, "error": "channel backend returned an empty response"}
        except ValueError:
            return {"success": False, "error": "channel backend returned a non-JSON response"}

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
