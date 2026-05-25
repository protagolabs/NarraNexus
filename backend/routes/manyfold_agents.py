"""
@file_name: manyfold_agents.py
@author: NexusAgent
@date: 2026-05-25
@description: Cross-user agent listing endpoint for Manyfold platform

Manyfold needs to enumerate all agents in the container regardless of
which NarraNexus user created them. The local /api/auth/agents endpoint
applies per-user filtering; this endpoint deliberately does not.

Registered only when ENABLE_MANYFOLD_API=1 (see backend/main.py). The
auth middleware requires a valid MANYFOLD_GATEWAY_TOKEN before the
handler runs.

Owner decision 2026-05-25: container is single-user in practice so the
cross-user concern is mostly cosmetic, but the platform contract still
expects "list everything" semantics — we honor it.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from xyz_agent_context.utils.db_factory import get_db_client


router = APIRouter()


def _require_manyfold_auth(request: Request) -> None:
    if not getattr(request.state, "manyfold_authed", False):
        raise HTTPException(
            status_code=401,
            detail="missing or invalid MANYFOLD_GATEWAY_TOKEN",
        )


@router.get("/manyfold/agents")
async def list_all_agents(request: Request):
    """Return every agent row in the container, cross-user.

    Shape mirrors what Manyfold's frameworkOptions expects (id + name +
    description), plus created_by / created_at for traceability.
    """
    _require_manyfold_auth(request)

    db = await get_db_client()
    rows = await db.get("agents", {}) or []
    return {
        "data": [
            {
                "agent_id": row.get("agent_id"),
                "name": row.get("agent_name"),
                "description": row.get("agent_description"),
                "agent_type": row.get("agent_type"),
                "created_by": row.get("created_by"),
                "created_at": row.get("agent_create_time"),
                "is_public": bool(row.get("is_public", 0)),
            }
            for row in rows
        ],
        "object": "list",
    }
