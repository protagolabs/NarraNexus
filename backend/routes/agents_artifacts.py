"""
@file_name: agents_artifacts.py
@author: Bin Liang
@date: 2026-05-08
@description: REST endpoints for agent-emitted Artifact tabs.

Endpoints:
- GET    /{agent_id}/artifacts                       list (scope=session|pinned, session_id?)
- GET    /{agent_id}/artifacts/{aid}                 metadata + version list
- GET    /{agent_id}/artifacts/{aid}/v{n}/raw        raw content with strict CSP header
- PATCH  /{agent_id}/artifacts/{aid}                 { pinned?, title? }
- DELETE /{agent_id}/artifacts/{aid}                 hard delete (row + folder)
"""

from __future__ import annotations

import os
import shutil
from typing import List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from loguru import logger
from pydantic import BaseModel

from xyz_agent_context.repository.artifact_repository import ArtifactRepository
from xyz_agent_context.schema import Artifact, ArtifactVersion
from xyz_agent_context.settings import settings
from xyz_agent_context.utils.db_factory import get_db_client


router = APIRouter()


CSP_BY_KIND = {
    "text/html":                       "default-src 'none'; style-src 'unsafe-inline'; img-src data: blob:",
    "application/vnd.echarts+json":    "default-src 'none'",
    "text/csv":                        "default-src 'none'",
    "text/markdown":                   "default-src 'none'",
    "image/png":                       "default-src 'none'; img-src 'self'",
    "image/jpeg":                      "default-src 'none'; img-src 'self'",
    "application/pdf":                 "default-src 'none'; object-src 'self'",
}

SAFE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-origin",
}


class PatchArtifact(BaseModel):
    pinned: Optional[bool] = None
    title: Optional[str] = None


class ArtifactDetail(BaseModel):
    artifact: Artifact
    versions: List[ArtifactVersion]


@router.get("/{agent_id}/artifacts", response_model=List[Artifact])
async def list_artifacts(
    agent_id: str,
    scope: Literal["session", "pinned"] = Query("session"),
    session_id: Optional[str] = Query(None),
):
    """
    List artifacts for an agent.

    Args:
        agent_id: Agent scope.
        scope: 'session' (default) returns non-pinned artifacts for the given
               session_id; 'pinned' returns all pinned artifacts for the agent.
        session_id: Required when scope='session'.

    Returns:
        List of Artifact objects.
    """
    db = await get_db_client()
    repo = ArtifactRepository(db)
    if scope == "pinned":
        return await repo.list_pinned(agent_id)
    if not session_id:
        raise HTTPException(400, "session_id is required when scope=session")
    return await repo.list_by_session(agent_id, session_id)


@router.get("/{agent_id}/artifacts/{artifact_id}", response_model=ArtifactDetail)
async def get_artifact(agent_id: str, artifact_id: str):
    """
    Get artifact metadata and its version list.

    Args:
        agent_id: Agent scope (used to verify ownership).
        artifact_id: Artifact ID.

    Returns:
        ArtifactDetail containing the Artifact and its ArtifactVersion list.
    """
    db = await get_db_client()
    repo = ArtifactRepository(db)
    art = await repo.get_by_id(artifact_id)
    if art is None or art.agent_id != agent_id:
        raise HTTPException(404, "artifact not found")
    versions = await repo.list_versions(artifact_id)
    return ArtifactDetail(artifact=art, versions=versions)


@router.get("/{agent_id}/artifacts/{artifact_id}/v{version}/raw")
async def get_raw(agent_id: str, artifact_id: str, version: int):
    """
    Serve the raw artifact file with strict Content-Security-Policy headers.

    The CSP is selected per MIME kind — scripts are never permitted from
    an external src, preventing XSS from arbitrary agent-generated HTML.

    Args:
        agent_id: Agent scope (ownership check).
        artifact_id: Artifact ID.
        version: Version number (integer path segment after 'v').

    Returns:
        FileResponse with kind-specific CSP and SAFE_HEADERS.
    """
    db = await get_db_client()
    repo = ArtifactRepository(db)
    art = await repo.get_by_id(artifact_id)
    if art is None or art.agent_id != agent_id:
        raise HTTPException(404, "artifact not found")
    versions = await repo.list_versions(artifact_id)
    match = next((v for v in versions if v.version == version), None)
    if match is None:
        raise HTTPException(404, "version not found")

    abs_path = os.path.join(settings.base_working_path, match.file_path)
    if not os.path.isfile(abs_path):
        logger.warning(f"Artifact file missing on disk: {abs_path}")
        raise HTTPException(410, "artifact file missing on disk")

    headers = {
        **SAFE_HEADERS,
        "Content-Security-Policy": CSP_BY_KIND.get(art.kind, "default-src 'none'"),
    }
    return FileResponse(path=abs_path, media_type=art.kind, headers=headers)


@router.patch("/{agent_id}/artifacts/{artifact_id}", response_model=Artifact)
async def patch_artifact(agent_id: str, artifact_id: str, body: PatchArtifact):
    """
    Update artifact metadata (pinned flag and/or title).

    Pinning an artifact clears its session_id, making it agent-scoped.

    Args:
        agent_id: Agent scope (ownership check).
        artifact_id: Artifact ID.
        body: Fields to update (all optional).

    Returns:
        Updated Artifact.
    """
    db = await get_db_client()
    repo = ArtifactRepository(db)
    existing = await repo.get_by_id(artifact_id)
    if existing is None or existing.agent_id != agent_id:
        raise HTTPException(404, "artifact not found")

    if body.pinned is not None:
        await repo.set_pinned(artifact_id, pinned=body.pinned)
    if body.title is not None:
        await db.update("instance_artifacts", {"artifact_id": artifact_id}, {"title": body.title[:200]})

    return await repo.get_by_id(artifact_id)


@router.delete("/{agent_id}/artifacts/{artifact_id}")
async def delete_artifact(agent_id: str, artifact_id: str):
    """
    Hard-delete an artifact: removes the DB row cascade and the on-disk folder.

    Args:
        agent_id: Agent scope (ownership check).
        artifact_id: Artifact ID.

    Returns:
        Dict with 'deleted' key containing the artifact_id.
    """
    db = await get_db_client()
    repo = ArtifactRepository(db)
    existing = await repo.get_by_id(artifact_id)
    if existing is None or existing.agent_id != agent_id:
        raise HTTPException(404, "artifact not found")

    await repo.delete(artifact_id)

    folder = os.path.join(
        settings.base_working_path,
        f"{existing.agent_id}_{existing.user_id}",
        "artifacts",
        artifact_id,
    )
    if os.path.isdir(folder):
        shutil.rmtree(folder, ignore_errors=True)
        logger.info(f"Deleted artifact folder: {folder}")

    return {"deleted": artifact_id}
