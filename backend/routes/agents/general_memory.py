"""
@file_name: general_memory.py
@author:
@date: 2026-08-10
@description: General-memory endpoints for the MCP data-access seam (PR-2).

Backend counterparts of the GeneralMemoryModule MCP tools (remember /
grep_memory / memory_retain) so the Http path of AgentDataStore can serve
them without db credentials in the mcp container. Implemented in this PR.

Endpoints (mounted under /api/agents by agents/core.py):
  GET  /{agent_id}/memory/remember  ?query=&limit=15
  GET  /{agent_id}/memory/grep      ?pattern=&regex=false&limit=30
  POST /{agent_id}/memory/retain    {content, source}

Each mirrors the matching MCP tool in
``_general_memory_mcp_tools.py`` byte-for-byte on call shape (same
MemoryCoordinator(MemoryEngine(db, agent_id)) construction, same
``_format`` hit rendering, same response dict keys) so an HttpStore-backed
caller gets identical payloads to the in-process MCP path.
"""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Query, Request
from loguru import logger
from pydantic import BaseModel, Field

from xyz_agent_context.memory import MemoryCoordinator, MemoryEngine, MemoryRecord, SCOPE_AGENT
from xyz_agent_context.utils.db.db_factory import get_db_client

# Ownership gate (backend/routes/_ownership.py): agent_id is attacker-
# controlled input — without the owner check a cross-tenant IDOR opens up
# (read/write another user's memory). Local mode (no JWT identity) does not
# enforce; see the helper's security-posture docstring before relying on it.
from backend.routes._ownership import assert_owned

router = APIRouter()


class MemoryRetainBody(BaseModel):
    # 64KB cap: the MCP twin takes model-generated content; over HTTP the
    # field is caller-controlled and this family bounds every free-text input.
    content: str = Field(min_length=1, max_length=65536)
    source: str = Field(default="", max_length=512)


def _format(hits: List[Any]) -> List[Dict[str, Any]]:
    """Render hits for the caller — text + provenance (which kind, when).

    Kept in lockstep with ``_general_memory_mcp_tools._format``: same kind /
    memory / when / tags / source keys, so the HTTP and MCP paths return
    identical shapes for the same underlying hits.
    """
    out: List[Dict[str, Any]] = []
    for h in hits:
        r = h.record
        item: Dict[str, Any] = {
            "kind": h.kind,
            "memory": r.content_text,
            "when": (r.created_at.isoformat() if r.created_at else None),
            "tags": r.tags,
        }
        if r.source_ref:
            item["source"] = r.source_ref
        out.append(item)
    return out


@router.get("/{agent_id}/memory/remember")
async def remember(
    request: Request,
    agent_id: str,
    query: str = Query(min_length=1, max_length=512),
    limit: int = Query(15, ge=1, le=100),
) -> dict:
    """Cross-kind ranked recall by meaning. Mirrors the ``remember`` MCP tool."""
    await assert_owned(request, agent_id)
    try:
        db = await get_db_client()
        coord = MemoryCoordinator(MemoryEngine(db, agent_id))
        hits = await coord.remember(query, limit=limit)
        return {"success": True, "query": query, "memories": _format(hits)}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[memory.remember] failed for agent {agent_id}: {e}")
        return {"success": False, "error": str(e), "memories": []}


@router.get("/{agent_id}/memory/grep")
async def grep_memory(
    request: Request,
    agent_id: str,
    pattern: str = Query(min_length=1, max_length=256),
    regex: bool = False,
    limit: int = Query(30, ge=1, le=200),
) -> dict:
    """Substring search over memory content. Mirrors the ``grep_memory`` MCP
    tool — EXCEPT regex mode, which is refused here: the engine compiles the
    caller's pattern with the stdlib re and runs it synchronously, and a
    catastrophic-backtracking pattern ((a+)+$-class, empirically >30s on one
    40-char record) would wedge the shared API event loop for every user. The
    MCP twin keeps regex because it runs in the per-module process driven by
    the agent's own LLM; publishing it on the shared API is what turns it
    into a self-service DoS. Migrating grep's Http path (PR-3) must first
    swap in a timeout-capable engine (the `regex` package / re2) — recorded
    in the mirror md."""
    await assert_owned(request, agent_id)
    if regex:
        return {
            "success": False,
            "error": (
                "regex mode is not available over HTTP; use a plain substring "
                "pattern"
            ),
            "matches": [],
        }
    try:
        db = await get_db_client()
        coord = MemoryCoordinator(MemoryEngine(db, agent_id))
        hits = await coord.grep_memory(pattern, regex=False, limit=limit)
        return {"success": True, "pattern": pattern, "matches": _format(hits)}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[memory.grep_memory] failed for agent {agent_id}: {e}")
        return {"success": False, "error": str(e), "matches": []}


@router.post("/{agent_id}/memory/retain")
async def memory_retain(request: Request, agent_id: str, body: MemoryRetainBody) -> dict:
    """Write one durable fact into long-term memory. Mirrors the ``memory_retain`` MCP tool."""
    await assert_owned(request, agent_id)
    try:
        content = body.content
        if not content or not content.strip():
            return {"success": False, "error": "content is empty"}
        db = await get_db_client()
        engine = MemoryEngine(db, agent_id)
        tags = ["imported"] if body.source else []
        rec = await engine.retain(MemoryRecord(
            agent_id=agent_id, scope_type=SCOPE_AGENT, kind="observation",
            subtype="world", content_text=content.strip(),
            tags=tags, proof_count=1,
            source_ref={"kind": "import", "id": body.source} if body.source else None,
        ))
        return {"success": True, "record_id": rec.record_id}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[memory.memory_retain] failed for agent {agent_id}: {e}")
        return {"success": False, "error": str(e)}
