"""
@file_name: jobs.py
@author:
@date: 2026-08-10
@description: Job READ endpoints for the MCP data-access seam (agent-scoped,
owner-gated).

Byte-parity Http twins of the JobModule read tools (job_retrieval_by_id /
_semantic / _by_keywords) so the HttpStore path of AgentDataStore can serve
them without db credentials in the mcp container. Each endpoint returns the
EXACT dict the seam's DirectStore returns — both call the shared, dialect-safe
``xyz_agent_context.module.job_module`` read helpers — so the Http and
in-process paths are byte-identical.

Distinct from ``backend/routes/jobs.py`` (the frontend-facing job API under
``/jobs`` with response_model shapes): these live under
``/api/agents/{agent_id}/jobs`` and are owner-gated like the other seam routes.
Searches are POST (their args — keyword lists, filters — travel in the body);
by-id is a GET.

user_id scoping — DELIBERATELY different from ``backend/routes/jobs.py``:
these search endpoints accept an OPTIONAL ``user_id`` from the caller (default
None = all users' jobs under the agent), because their caller is the agent
itself (via the identity-forwarded seam) querying its OWN agent's jobs — and
this exactly preserves the MCP tool's signature (``user_id: Optional[str]``), so
DirectStore and HttpStore stay byte-identical. ``backend/routes/jobs.py`` makes
the OPPOSITE choice (user_id forced to the authenticated caller) on purpose:
that surface is a user's browser dashboard, where one user must not read another
user's jobs. Both are correct for their actor; ``assert_owned`` gates these to
the agent's owner, who already has this access through their own agent.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Request
from loguru import logger
from pydantic import BaseModel, Field

from backend.routes._ownership import assert_owned
from xyz_agent_context.module.job_module import (
    fetch_job_by_id,
    search_jobs_semantic,
    search_jobs_by_keywords,
)
from xyz_agent_context.utils.db.db_factory import get_db_client

router = APIRouter()


class JobSemanticSearchBody(BaseModel):
    query: str = Field(min_length=1, max_length=512)
    user_id: Optional[str] = None
    status: Optional[str] = None
    limit: int = Field(default=10, ge=1, le=100)


class JobKeywordSearchBody(BaseModel):
    keywords: List[str] = Field(min_length=1)
    user_id: Optional[str] = None
    status: Optional[str] = None
    limit: int = Field(default=20, ge=1, le=100)


@router.get("/{agent_id}/jobs/{job_id}")
async def job_by_id(agent_id: str, job_id: str, request: Request) -> dict:
    """Full detail of one job — twin of the ``job_retrieval_by_id`` MCP tool."""
    await assert_owned(request, agent_id)
    try:
        return await fetch_job_by_id(await get_db_client(), agent_id, job_id)
    except Exception as e:  # noqa: BLE001 — get_db_client() only; fetch never raises
        logger.warning(f"job_by_id failed: {e}")
        return {"success": False, "error": str(e)}


@router.post("/{agent_id}/jobs/search-semantic")
async def job_search_semantic(agent_id: str, body: JobSemanticSearchBody, request: Request) -> dict:
    """Keyword (BM25) job search — twin of the ``job_retrieval_semantic`` tool."""
    await assert_owned(request, agent_id)
    try:
        return await search_jobs_semantic(
            await get_db_client(), agent_id, body.query, body.user_id, body.status, body.limit,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"job_search_semantic failed: {e}")
        return {"success": False, "error": str(e)}


@router.post("/{agent_id}/jobs/search-keywords")
async def job_search_keywords(agent_id: str, body: JobKeywordSearchBody, request: Request) -> dict:
    """Keyword-list job search — twin of the ``job_retrieval_by_keywords`` tool."""
    await assert_owned(request, agent_id)
    try:
        return await search_jobs_by_keywords(
            await get_db_client(), agent_id, body.keywords, body.user_id, body.status, body.limit,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"job_search_keywords failed: {e}")
        return {"success": False, "error": str(e)}
