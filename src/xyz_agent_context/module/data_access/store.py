"""
@file_name: store.py
@author:
@date: 2026-08-10
@description: AgentDataStore — the data-access abstraction MCP tools depend on.

Blueprint P0: an MCP tool no longer reaches into repositories/db directly; it
calls this interface. Two implementations, chosen by the composition root
(factory.get_agent_data_store, keyed on NARRANEXUS_BACKEND_URL):

- DirectStore: direct repository access — the CURRENT behavior, byte-for-byte.
  Used locally (`bash run.sh` / DMG) where the process owns the sqlite db.
- HttpStore: calls the backend API, forwarding the caller identity. Used in
  cloud so the mcp container holds NO db credentials (the RCE-remediation goal).

The interface grows one method per migrated tool; awareness's update is first.
Rule #9/#20: tools route through a seam, not a hardcoded call, so Direct↔Http
swaps with no tool change. Rule #21: HttpStore reaches backend over HTTP (never
an import) — the allowed one-way hop.

The backend response contract every Http method must honor
--------------------------------------------------------
The agents routes report failure as **HTTP 200 with {"success": false,
"error": ...}** (they reserve non-2xx for transport/middleware layers, e.g.
the Q6 identity 401). An Http method that checks only the status code
therefore reports every backend failure as success — parse the body. And a
method must NEVER let an HTTP error escape as an exception: DirectStore only
ever returns strings, so the Http path degrades to an in-band "Error: ..."
the model can read (a 401 here means the deploy set NARRANEXUS_BACKEND_URL
before provisioning the identity keys — see factory.py).
"""
from __future__ import annotations

from typing import Optional, Protocol

from loguru import logger


class AgentDataStore(Protocol):
    """Data operations an MCP tool needs, transport-agnostic."""

    async def update_awareness(self, agent_id: str, awareness: str) -> str: ...


# Return strings the awareness MCP tool has always produced — DirectStore and
# HttpStore MUST both yield these so migration is behaviour-preserving (parity).
_AWARENESS_OK = "Awareness updated successfully"


def _no_instance_msg(agent_id: str) -> str:
    return f"Error: No AwarenessModule instance found for agent_id={agent_id}"


class DirectStore:
    """Local: direct repository access — unchanged from the pre-abstraction tool."""

    async def _db(self):
        # The one MCP db entry point (module/base.py) — loop-aware factory
        # semantics documented there; every other MCP tool goes through it.
        from xyz_agent_context.module.base import XYZBaseModule

        return await XYZBaseModule.get_mcp_db_client()

    async def _awareness_instance_id(self, db, agent_id: str) -> Optional[str]:
        from xyz_agent_context.repository import InstanceRepository

        instances = await InstanceRepository(db).get_by_agent(
            agent_id=agent_id, module_class="AwarenessModule"
        )
        return instances[0].instance_id if instances else None

    async def update_awareness(self, agent_id: str, awareness: str) -> str:
        from xyz_agent_context.repository import InstanceAwarenessRepository

        db = await self._db()
        instance_id = await self._awareness_instance_id(db, agent_id)
        if not instance_id:
            return _no_instance_msg(agent_id)
        await InstanceAwarenessRepository(db).upsert(instance_id, awareness)
        return _AWARENESS_OK


class HttpStore:
    """Cloud: call the backend API (no db creds in mcp).

    Forwards the caller identity so the backend can authenticate the call
    (blueprint Q6 — the nx-agent bearer's identity_token is verified there;
    per-route OWNER checks are PR-2's work, not yet in place).
    ``identity_headers`` is the same header set the executor→mcp hop already
    carries; see factory.get_agent_data_store for wiring.

    Every method sends ``create_missing=false``-style parity switches where
    the backend route's convenience semantics (auto-create) diverge from the
    direct path, and parses the 200+success:false failure shape — see the
    module docstring.
    """

    def __init__(self, backend_url: str, identity_headers: Optional[dict] = None) -> None:
        self._base = backend_url.rstrip("/")
        self._headers = identity_headers or {}

    async def update_awareness(self, agent_id: str, awareness: str) -> str:
        import httpx

        try:
            async with httpx.AsyncClient(
                base_url=self._base, headers=self._headers, timeout=20.0
            ) as c:
                r = await c.put(
                    f"/api/agents/{agent_id}/awareness",
                    params={"create_missing": "false"},
                    json={"awareness": awareness},
                )
        except httpx.HTTPError as e:
            logger.warning(f"[data-access] awareness backend unreachable: {e}")
            return f"Error: awareness backend unreachable ({type(e).__name__})"
        if r.status_code >= 400:
            # Transport/middleware-layer rejection (the route itself always
            # answers 200) — most likely the Q6 identity gate. In-band, never
            # an exception: the direct path only ever returns strings.
            logger.warning(
                f"[data-access] awareness backend rejected the call: {r.status_code}"
            )
            return f"Error: awareness backend rejected the call ({r.status_code})"
        try:
            body = r.json() or {}
        except ValueError:
            return "Error: awareness backend returned a non-JSON response"
        if not body.get("success"):
            error = str(body.get("error") or "unknown backend error")
            return error if error.startswith("Error:") else f"Error: {error}"
        return _AWARENESS_OK
