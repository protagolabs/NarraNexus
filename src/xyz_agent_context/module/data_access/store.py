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

The interface grows one method-pair per migrated module; awareness is first.
Rule #9/#20: tools route through a seam, not a hardcoded call, so Direct↔Http
swaps with no tool change. Rule #21: HttpStore reaches backend over HTTP (never
an import) — the allowed one-way hop.
"""
from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class AgentDataStore(Protocol):
    """Data operations an MCP tool needs, transport-agnostic."""

    async def get_awareness(self, agent_id: str) -> Optional[str]: ...

    async def update_awareness(self, agent_id: str, awareness: str) -> str: ...


# Return strings the awareness MCP tool has always produced — DirectStore and
# HttpStore MUST both yield these so migration is behaviour-preserving (parity).
_AWARENESS_OK = "Awareness updated successfully"


def _no_instance_msg(agent_id: str) -> str:
    return f"Error: No AwarenessModule instance found for agent_id={agent_id}"


class DirectStore:
    """Local: direct repository access — unchanged from the pre-abstraction tool."""

    async def _db(self):
        from xyz_agent_context.utils.db.db_factory import get_db_client

        return await get_db_client()

    async def _awareness_instance_id(self, db, agent_id: str) -> Optional[str]:
        from xyz_agent_context.repository import InstanceRepository

        instances = await InstanceRepository(db).get_by_agent(
            agent_id=agent_id, module_class="AwarenessModule"
        )
        return instances[0].instance_id if instances else None

    async def get_awareness(self, agent_id: str) -> Optional[str]:
        db = await self._db()
        instance_id = await self._awareness_instance_id(db, agent_id)
        if not instance_id:
            return None
        row = await db.get_one("instance_awareness", {"instance_id": instance_id})
        return (row or {}).get("awareness")

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

    Forwards the caller identity so the backend can owner-scope the operation.
    ``identity_headers`` is the same header set the executor→mcp hop already
    carries (X-NarraNexus-* / nx-agent bearer); the backend's service-trust path
    (blueprint Q6) verifies it. See factory.get_agent_data_store for wiring.
    """

    def __init__(self, backend_url: str, identity_headers: Optional[dict] = None) -> None:
        self._base = backend_url.rstrip("/")
        self._headers = identity_headers or {}

    async def _client(self):
        import httpx

        return httpx.AsyncClient(base_url=self._base, headers=self._headers, timeout=20.0)

    async def get_awareness(self, agent_id: str) -> Optional[str]:
        async with await self._client() as c:
            r = await c.get(f"/api/agents/{agent_id}/awareness")
            r.raise_for_status()
            return (r.json() or {}).get("awareness")

    async def update_awareness(self, agent_id: str, awareness: str) -> str:
        async with await self._client() as c:
            r = await c.put(
                f"/api/agents/{agent_id}/awareness", json={"awareness": awareness}
            )
            if r.status_code == 404:
                return _no_instance_msg(agent_id)
            r.raise_for_status()
            return _AWARENESS_OK
