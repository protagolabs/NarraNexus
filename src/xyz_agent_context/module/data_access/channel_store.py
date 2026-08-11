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
  credential manager (from its ``ChannelSpec``) and returns the dataclass
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

All 7 channels are wired through ONE ``ChannelSpec`` descriptor per channel (the
``CHANNELS`` table) — manager + read-method + unbind display-name + optional
bind service, each a lazily-resolved name. Adding a channel is genuinely one
``ChannelSpec(...)`` line + a ``to_raw_dict()`` on its dataclass; nothing here
can drift because there is a single source of truth, not parallel dicts.

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

from dataclasses import dataclass
from typing import Any, Optional, Protocol
from urllib.parse import quote

from loguru import logger


def _seg(value: str) -> str:
    """Percent-encode an id used as a URL PATH SEGMENT — see store.py's ``_seg``
    for the full rationale (LLM-supplied ids may contain ``?``/``#``/``..``)."""
    return quote(str(value), safe="")


def deep_merge(base: dict, patch: dict) -> dict:
    """Merge ``patch`` INTO a copy of ``base``: where BOTH sides hold a dict,
    recurse (so a ``patch_credential`` of ``{"permission_state": {k: v}}`` adds
    ``k`` to the existing blob instead of replacing the whole blob); otherwise
    ``patch`` wins. This is the one shared semantics the credential-mutation
    ``PATCH`` primitive promises — a manager's ``apply_patch`` uses it so the
    local and HTTP paths merge identically."""
    out = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


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

    # Generic credential-mutation primitives (blueprint P2 lark write leg). Any
    # channel whose manager implements ``apply_patch`` / ``save_raw`` /
    # ``delete_credential`` can be written through these without a per-op route —
    # lark's CLI-OAuth flow is the first user. See the module docstring.
    async def patch_credential(self, channel: str, agent_id: str, patch: dict) -> dict: ...

    async def put_credential(self, channel: str, agent_id: str, raw: dict) -> dict: ...

    async def delete_credential(self, channel: str, agent_id: str) -> dict: ...


# One per-channel descriptor — the SINGLE source of truth. Everything (reads,
# the write-leg, the endpoint allowlist, unbind wording) reads one field of it,
# so there are no parallel channel-keyed dicts to drift out of sync. Adding a
# channel is genuinely one ``ChannelSpec(...)`` line + a ``to_raw_dict()`` on
# its dataclass. All resolution is lazy by NAME (module path strings) so this
# module never imports every channel package just to hold the table.


@dataclass(frozen=True)
class _BindSpec:
    """A channel's do_bind service — present only for channels with a bind tool.

    ``do_bind`` is NOT uniform: discord/slack/telegram take the manager,
    narramessenger's gateway-bind takes the raw db (``takes``). ``do_test_connection``
    lives in the SAME service module, so the status live-check resolves from here
    too (``has_test``)."""

    service_module: str
    do_bind: str = "do_bind"
    takes: str = "mgr"  # "mgr" | "db"
    has_test: bool = True  # service module also defines do_test_connection


@dataclass(frozen=True)
class ChannelSpec:
    manager_module: str
    manager_class: str
    read_method: str = "get"  # "get" | "get_credential" (lark)
    # Backend /unbind route's not-found wording ("no <display_name> credential
    # bound…") — set ONLY for channels whose unbind is reachable through the seam
    # (they have a ``<ch>_unbind`` MCP tool). "" means DirectStore.unbind falls
    # back to the raw channel string (fine: those channels never call it).
    display_name: str = ""
    bind: Optional[_BindSpec] = None  # None → no bind tool (wechat QR / lark CLI / HA panel)


_CM = "xyz_agent_context.module"
CHANNELS: dict[str, ChannelSpec] = {
    "discord": ChannelSpec(
        f"{_CM}.discord_module._discord_credential_manager", "DiscordCredentialManager",
        display_name="Discord",
        bind=_BindSpec(f"{_CM}.discord_module._discord_service"),
    ),
    "slack": ChannelSpec(
        f"{_CM}.slack_module._slack_credential_manager", "SlackCredentialManager",
        display_name="Slack",
        bind=_BindSpec(f"{_CM}.slack_module._slack_service"),
    ),
    "telegram": ChannelSpec(
        f"{_CM}.telegram_module._telegram_credential_manager", "TelegramCredentialManager",
        display_name="Telegram",
        bind=_BindSpec(f"{_CM}.telegram_module._telegram_service"),
    ),
    "wechat": ChannelSpec(
        f"{_CM}.wechat_module._wechat_credential_manager", "WeChatCredentialManager",
        display_name="WeChat",  # has an unbind tool; bind is QR (backend-only), so no _BindSpec
    ),
    "narramessenger": ChannelSpec(
        f"{_CM}.narramessenger_module._narramessenger_credential_manager", "NarramessengerCredentialManager",
        # bind tool exists (gateway-bind takes the raw db); NO unbind tool + no
        # do_test_connection, so display_name="" and has_test=False.
        bind=_BindSpec(f"{_CM}.narramessenger_module._narramessenger_service", takes="db", has_test=False),
    ),
    "lark": ChannelSpec(
        f"{_CM}.lark_module._lark_credential_manager", "LarkCredentialManager",
        read_method="get_credential",
        # bind/unbind + the CLI-OAuth writes go through the credential-mutation
        # primitives (patch/put/delete), not a typed do_bind — so no _BindSpec.
    ),
    # Home Assistant has no bot credential — a JSON config blob (base_url + LLAT).
    # A thin repository-backed adapter gives it the seam's uniform read shape.
    "home_assistant": ChannelSpec(
        f"{_CM}.home_assistant_module._home_assistant_impl.binding", "HomeAssistantCredentialManager",
    ),
}

# The allowlist the backend endpoint gates on — derived from the descriptor so
# it can never drift from what DirectStore can actually resolve.
SUPPORTED_CHANNELS = frozenset(CHANNELS)


def _spec(channel: str) -> ChannelSpec:
    spec = CHANNELS.get(channel)
    if spec is None:
        raise ValueError(f"unknown channel: {channel!r}")
    return spec


def _manager_class(channel: str):
    """Resolve ``channel`` to its credential-manager class, importing lazily."""
    import importlib

    spec = _spec(channel)
    return getattr(importlib.import_module(spec.manager_module), spec.manager_class)


def _read_method_name(channel: str) -> str:
    """The manager's read method for ``channel`` (get / get_credential / …)."""
    return _spec(channel).read_method


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
        the ChannelSpec's ``_BindSpec.takes`` records which. Returns do_bind's
        {success, error?, data?}."""
        bind = _spec(channel).bind
        if bind is None:
            raise ValueError(f"channel {channel!r} has no bind service")
        import importlib

        do_bind = getattr(importlib.import_module(bind.service_module), bind.do_bind)
        db = await self._db()
        if bind.takes == "mgr":
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
            name = _spec(channel).display_name or channel
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

        # A channel with no bind service (wechat/lark/home_assistant) or one
        # whose service has no do_test_connection (narramessenger, has_test=False)
        # gets a clear ValueError — never a bare KeyError / AttributeError.
        bind = _spec(channel).bind
        do_test = (
            getattr(importlib.import_module(bind.service_module), "do_test_connection", None)
            if bind is not None and bind.has_test
            else None
        )
        if do_test is None:
            raise ValueError(
                f"channel {channel!r} has no test_connection (only discord/slack/telegram wire one)"
            )
        return await do_test((_manager_class(channel))(await self._db()), agent_id)

    # -- credential-mutation primitives (delegate to the manager's write API) --

    async def patch_credential(self, channel: str, agent_id: str, patch: dict) -> dict:
        """Partial update — the manager deep-merges (nested dicts like
        permission_state merged key-wise) and saves."""
        await (_manager_class(channel))(await self._db()).apply_patch(agent_id, patch)
        return {"success": True}

    async def put_credential(self, channel: str, agent_id: str, raw: dict) -> dict:
        """Full upsert of a raw cred dict — the manager rebuilds its dataclass
        (its own _cred_from_raw) and saves."""
        await (_manager_class(channel))(await self._db()).save_raw(agent_id, raw)
        return {"success": True}

    async def delete_credential(self, channel: str, agent_id: str) -> dict:
        await (_manager_class(channel))(await self._db()).delete_credential(agent_id)
        return {"success": True, "data": {"deleted": True}}


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

    # -- credential-mutation primitives: PATCH/PUT/DELETE the generic endpoint --

    def _cred_path(self, channel: str, agent_id: str) -> str:
        return f"/api/agents/{_seg(agent_id)}/channels/{_seg(channel)}/credential"

    async def patch_credential(self, channel: str, agent_id: str, patch: dict) -> dict:
        return await self._send_json("PATCH", self._cred_path(channel, agent_id), patch)

    async def put_credential(self, channel: str, agent_id: str, raw: dict) -> dict:
        return await self._send_json("PUT", self._cred_path(channel, agent_id), raw)

    async def delete_credential(self, channel: str, agent_id: str) -> dict:
        return await self._send_json("DELETE", self._cred_path(channel, agent_id), None)

    async def _post_json(self, path: str, body: dict) -> dict:
        return await self._send_json("POST", path, body)

    async def _send_json(self, method: str, path: str, body: Optional[dict]) -> dict:
        """One HTTP-verb + HTTPError boundary for every WRITE. Writes must ALWAYS
        hand back a {success, error?} dict the tool can relay — never raise, never
        None (a failed unbind is not "unbound") — so transport failures / non-2xx /
        non-JSON all degrade to success:False with a readable reason."""
        import httpx

        try:
            async with httpx.AsyncClient(
                base_url=self._base, headers=self._headers, timeout=20.0
            ) as c:
                r = await c.request(method, path, json=body)
        except httpx.HTTPError as e:
            logger.warning(f"[channel-store] backend unreachable {method} {path}: {e}")
            return {"success": False, "error": f"channel backend unreachable ({type(e).__name__})"}
        if r.status_code >= 400:
            logger.warning(f"[channel-store] backend rejected {method} {path}: {r.status_code}")
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
