"""
@file_name: narrative.py
@author:
@date: 2026-08-10
@description: Narrative endpoints for the MCP data-access seam (PR-2).

Backend counterparts of the BasicInfoModule narrative MCP tools
(view_narrative / switch_narrative / create_narrative,
_basic_info_mcp_tools.py) so the Http path of AgentDataStore can serve them
without db credentials in the mcp container.

- GET  /{agent_id}/narratives/{narrative_id}        -> view_narrative
- POST /{agent_id}/narratives/{narrative_id}/switch -> switch_narrative
- POST /{agent_id}/narratives                       -> create_narrative

Every endpoint calls ``assert_owned`` first (403 non-owner / 404 unknown
agent / no-op in local mode), matching the other MCP data-access-seam
routes (awareness.py, general_memory.py).

``create_narrative`` differs from its MCP-tool namesake on purpose: the MCP
tool is SIGNAL-ONLY (the agent_runtime hook does the actual create — see
``step_4_persist_results._detect_narrative_routing_signal``), because the
tool call and the runtime run in different processes and can't share state
any other way. This HTTP endpoint has no such constraint and no runtime
hook to defer to, so it creates the row directly via
``NarrativeService.create_narrative`` and returns a real ``narrative_id``.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request
from loguru import logger
from pydantic import BaseModel, Field

from backend.auth import resolve_current_user_id
from backend.routes._ownership import assert_owned
from xyz_agent_context.narrative import NarrativeService
from xyz_agent_context.utils.db.db_factory import get_db_client

router = APIRouter()


def _parse_info(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw) or {}
        except Exception:  # noqa: BLE001
            return {}
    return {}


async def _narrative_chat_history(db, narrative_id: str, limit: int = 200) -> List[Dict[str, Any]]:
    """Full chat history of a narrative from its ChatModule instances.

    Same data + shape as
    ``basic_info_module._basic_info_mcp_tools._narrative_chat_history``, but
    reimplemented on ``AsyncDatabaseClient`` helpers instead of raw SQL —
    routes must not hand-write SQL (unlike the in-process MCP tool, which
    has direct db credentials the Http path deliberately doesn't expose).
    """
    links = await db.get(
        "instance_narrative_links", filters={"narrative_id": narrative_id}, limit=200
    )
    inst_ids = [row.get("instance_id") for row in (links or [])]
    # Bounded fan-out with ONE query (BaseRepository exists to kill N+1): the
    # MCP twin ran in the module process where a fat narrative only slowed its
    # own agent, but on the shared API process a per-instance loop is a slow
    # request for everyone. 100 chat instances is far beyond any real narrative.
    inst_ids = [i for i in inst_ids if i and i.startswith("chat_")][:100]

    messages: List[Dict[str, Any]] = []
    mrows = await db.get_by_ids(
        "instance_json_format_memory_chat", "instance_id", inst_ids
    ) if inst_ids else []
    for mrow in mrows:
        mem = _parse_info(mrow.get("memory"))
        for m in mem.get("messages", []):
            meta = m.get("meta_data", {}) or {}
            messages.append({
                "time": str(meta.get("timestamp", ""))[:19],
                "role": m.get("role"),
                "content": (m.get("content") or "")[:2000],
                "event_id": meta.get("event_id"),
            })
    messages.sort(key=lambda x: x.get("time", ""))
    return messages[-limit:]


class NarrativeViewResponse(BaseModel):
    success: bool
    narrative_id: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    summary: Optional[str] = None
    keywords: Optional[List[str]] = None
    message_count: Optional[int] = None
    messages: Optional[List[Dict[str, Any]]] = None
    error: Optional[str] = None


class NarrativeSwitchResponse(BaseModel):
    success: bool
    narrative_id: Optional[str] = None
    message: Optional[str] = None
    error: Optional[str] = None


class CreateNarrativeRequest(BaseModel):
    # No user_id field: the narrative's owner is the AUTHENTICATED caller
    # (resolve_current_user_id), never a body-supplied id — assert_owned only
    # proves agent ownership, and a trusted-body user_id would let an owner
    # attribute rows to arbitrary users (pre-open review #6).
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=2000)


class NarrativeCreateResponse(BaseModel):
    success: bool
    narrative_id: Optional[str] = None
    title: Optional[str] = None
    error: Optional[str] = None


@router.get("/{agent_id}/narratives/{narrative_id}", response_model=NarrativeViewResponse)
async def view_narrative(agent_id: str, narrative_id: str, request: Request):
    """Full info on one narrative (thread) including its entire chat
    history — the Http counterpart of the ``view_narrative`` MCP tool."""
    await assert_owned(request, agent_id)
    try:
        db = await get_db_client()
        row = await db.get_one("narratives", filters={"narrative_id": narrative_id})
        if not row or row.get("agent_id") != agent_id:
            return NarrativeViewResponse(success=False, error=f"narrative {narrative_id} not found")

        info = _parse_info(row.get("narrative_info"))
        kws = row.get("topic_keywords")
        keywords = kws if isinstance(kws, list) else (_parse_info(kws) or [])
        history = await _narrative_chat_history(db, narrative_id)
        logger.info(f"[NarrativeRoute] view_narrative({narrative_id}) -> {len(history)} messages")
        return NarrativeViewResponse(
            success=True,
            narrative_id=narrative_id,
            name=info.get("name"),
            description=info.get("description"),
            summary=info.get("current_summary"),
            keywords=keywords if isinstance(keywords, list) else [],
            message_count=len(history),
            messages=history,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception(f"view_narrative failed: {e}")
        return NarrativeViewResponse(success=False, error=str(e))


@router.post("/{agent_id}/narratives/{narrative_id}/switch", response_model=NarrativeSwitchResponse)
async def switch_narrative(agent_id: str, narrative_id: str, request: Request):
    """Validate that ``narrative_id`` exists and belongs to ``agent_id`` —
    the Http counterpart of the ``switch_narrative`` MCP tool.

    Like the MCP tool, this is a validation, not a re-attribution: turn
    re-filing only happens inside a live agent run
    (``step_4_persist_results``), which this route has no access to.
    """
    await assert_owned(request, agent_id)
    try:
        db = await get_db_client()
        row = await db.get_one("narratives", filters={"narrative_id": narrative_id})
        if not row or row.get("agent_id") != agent_id:
            return NarrativeSwitchResponse(success=False, error=f"narrative {narrative_id} not found")
        logger.info(f"[NarrativeRoute] switch_narrative -> {narrative_id} (agent={agent_id})")
        return NarrativeSwitchResponse(
            success=True,
            narrative_id=narrative_id,
            message="This turn will be attributed to this narrative.",
        )
    except Exception as e:  # noqa: BLE001
        logger.exception(f"switch_narrative failed: {e}")
        return NarrativeSwitchResponse(success=False, error=str(e))


@router.post("/{agent_id}/narratives", response_model=NarrativeCreateResponse)
async def create_narrative(agent_id: str, body: CreateNarrativeRequest, request: Request):
    """Create a new narrative (thread) for ``agent_id`` and return its id.

    See the module docstring for why this actually creates, unlike the
    signal-only ``create_narrative`` MCP tool.
    """
    await assert_owned(request, agent_id)
    if not (body.title or "").strip():
        return NarrativeCreateResponse(success=False, error="title is required")
    user_id = await resolve_current_user_id(request)
    try:
        db = await get_db_client()
        service = NarrativeService(agent_id=agent_id, database_client=db)
        narrative = await service.create_narrative(
            agent_id=agent_id,
            user_id=user_id,
            title=body.title,
            description=body.description,
        )
        logger.info(f"[NarrativeRoute] create_narrative -> {narrative.id} (agent={agent_id})")
        return NarrativeCreateResponse(success=True, narrative_id=narrative.id, title=body.title)
    except Exception as e:  # noqa: BLE001
        logger.exception(f"create_narrative failed: {e}")
        return NarrativeCreateResponse(success=False, error=str(e))
