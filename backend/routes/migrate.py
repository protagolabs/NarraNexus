"""
@file_name: migrate.py
@author: NetMind.AI
@date: 2026-07-21
@description: Agent Migration Scanner API (under /api/migrate) — LOCAL ONLY.

Detect + extract other-framework agent configs (Claude Code / Hermes /
OpenClaw / Codex) from the user's local filesystem into the standardized JSON.

**Local/desktop only.** In cloud mode the executor/backend is remote — there is
no user filesystem to scan — so every endpoint returns 503 `migration_local_only`.
Consumers (Import Button, Migration Skill) do the map+write; this route never
writes anything to NarraNexus.

Design: reference/self_notebook/specs/2026-07-21-agent-migration-tech-design.md
"""

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel

from xyz_agent_context.utils.deployment_mode import is_cloud_mode
from xyz_agent_context.migration import scanner
from xyz_agent_context.schema.migration_schema import Framework

router = APIRouter()


def _require_local_or_raise() -> None:
    if is_cloud_mode():
        raise HTTPException(
            status_code=503,
            detail="migration_local_only: the agent-import scanner reads your local "
            "filesystem and is only available in the desktop/local app.",
        )


class ScanRequest(BaseModel):
    # Explicit source dir (e.g. "~/.claude"). If omitted, auto-detect across the
    # standard home locations and scan the highest-confidence framework.
    path: Optional[str] = None
    framework: Optional[Framework] = None


@router.get("/detect")
async def detect(request: Request) -> dict:
    """List every known framework found in the standard home locations."""
    _require_local_or_raise()
    detections = scanner.detect()
    return {"detections": [d.model_dump() for d in detections]}


@router.post("/scan")
async def scan(request: Request, payload: ScanRequest) -> dict:
    """Scan one source into the standardized JSON (detect + extract, no write)."""
    _require_local_or_raise()
    try:
        result = scanner.scan(path=payload.path, framework=payload.framework)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"migrate.scan failed: {type(e).__name__}: {e}")
        raise HTTPException(status_code=400, detail=f"scan failed: {e}")
    logger.info(
        f"migrate.scan: framework={result.source.framework} "
        f"skills={len(result.skills)} memory={len(result.memory)} mcp={len(result.mcp_servers)}"
    )
    return result.model_dump()
