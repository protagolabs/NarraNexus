"""
@file_name: capabilities.py
@author: Bin Liang
@date: 2026-09-04
@description: Per-agent capability enablement (plugin platform batch 5c).

Endpoints (mounted under /api/agents by agents/core.py):
  GET    /{agent_id}/capabilities                  — every registered module with its state + the context budget
  PUT    /{agent_id}/capabilities/{module_class}   — {"enabled": bool}; base modules cannot be disabled
  DELETE /{agent_id}/capabilities/{module_class}   — back to the default rule (builtin on, plugin off)

Auth: the caller must OWN the agent (agents.created_by) — the same gate as
llm_config. A change applies on the agent's next run (the loader reads the
table per turn).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.auth import resolve_current_user_id
from narranexus.platform.module_system.capability_service import CapabilityService
from narranexus.platform.utils.db.db_factory import get_db_client

router = APIRouter()

_SAFE_ID_PATTERN = r"^[a-zA-Z0-9_\-]+$"


class EnabledBody(BaseModel):
    enabled: bool = Field(...)


async def _require_owner(agent_id: str, request: Request):
    user_id = await resolve_current_user_id(request)
    db = await get_db_client()
    agent_row = await db.get_one("agents", {"agent_id": agent_id})
    if not agent_row:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id!r} not found.")
    if agent_row.get("created_by") != user_id:
        raise HTTPException(status_code=403, detail="Only the agent's owner can change its capabilities.")
    return db


@router.get("/{agent_id}/capabilities")
async def get_capabilities(agent_id: str, request: Request) -> dict[str, Any]:
    db = await _require_owner(agent_id, request)
    return {"success": True, "data": await CapabilityService(db).overview(agent_id)}


@router.put("/{agent_id}/capabilities/{module_class}")
async def set_capability(agent_id: str, module_class: str, body: EnabledBody, request: Request) -> dict[str, Any]:
    db = await _require_owner(agent_id, request)
    try:
        result = await CapabilityService(db).set_enabled(agent_id, module_class, body.enabled)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return {"success": True, "data": result}


@router.delete("/{agent_id}/capabilities/{module_class}")
async def reset_capability(agent_id: str, module_class: str, request: Request) -> dict[str, Any]:
    db = await _require_owner(agent_id, request)
    removed = await CapabilityService(db).reset(agent_id, module_class)
    return {"success": True, "data": {"module_class": module_class, "reset": removed}}


__all__ = ["router"]
