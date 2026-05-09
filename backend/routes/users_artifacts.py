"""
@file_name: users_artifacts.py
@author: Bin Liang
@date: 2026-05-09
@description: User-scoped artifact endpoints — cross-agent list + bulk delete.

Backs the Settings → Artifacts management UI. Distinct from
agents_artifacts.py (agent-scoped routes) because the management UI needs
to see the full user-owned set across every agent the user owns, and
let them delete in bulk to free the per-user quota.

Endpoints:
- GET    /{user_id}/artifacts                   list every artifact owned by user
- GET    /{user_id}/artifacts/quota             return current usage vs. limits
- DELETE /{user_id}/artifacts                   bulk delete; body { artifact_ids: [...] }
"""

from __future__ import annotations

import os
import shutil
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel, Field

from xyz_agent_context.repository.artifact_repository import ArtifactRepository
from xyz_agent_context.schema import Artifact
from xyz_agent_context.settings import settings
from xyz_agent_context.utils.db_factory import get_db_client


router = APIRouter()


async def _verify_user_self(request: Request, user_id: str) -> None:
    """In cloud mode, the JWT user must match the path user_id.

    Local mode (no JWT / no request.state.user_id) skips enforcement.
    Cloud mode mismatch returns 403 to avoid leaking which user_ids exist.
    """
    if not hasattr(request.state, "user_id") or not request.state.user_id:
        return
    if request.state.user_id != user_id:
        raise HTTPException(403, "not authorized for this user")


class QuotaInfo(BaseModel):
    used_count: int
    count_limit: int
    used_bytes: int
    bytes_limit: int
    is_cloud_mode: bool


class BulkDeleteRequest(BaseModel):
    artifact_ids: List[str] = Field(default_factory=list, max_length=200)


class BulkDeleteResponse(BaseModel):
    deleted: int
    skipped_not_owned: List[str] = Field(default_factory=list)


@router.get("/{user_id}/artifacts", response_model=List[Artifact])
async def list_user_artifacts(user_id: str, request: Request):
    """List every artifact owned by the user, ordered by recency."""
    await _verify_user_self(request, user_id)
    db = await get_db_client()
    repo = ArtifactRepository(db)
    return await repo.list_by_user(user_id)


@router.get("/{user_id}/artifacts/quota", response_model=QuotaInfo)
async def get_user_quota(user_id: str, request: Request):
    """Return current artifact usage vs. configured limits.

    Used by the Settings → Artifacts panel to render the "8 / 10" headline
    and to colour the progress bar warning when the user is approaching
    the cap.
    """
    await _verify_user_self(request, user_id)
    db = await get_db_client()
    repo = ArtifactRepository(db)
    used_count = await repo.count_for_user(user_id)
    used_bytes = await repo.total_bytes_for_user(user_id)
    return QuotaInfo(
        used_count=used_count,
        count_limit=settings.artifact_count_limit_per_user,
        used_bytes=used_bytes,
        bytes_limit=settings.artifact_total_bytes_per_user,
        is_cloud_mode=settings.is_cloud_mode,
    )


@router.delete("/{user_id}/artifacts", response_model=BulkDeleteResponse)
async def bulk_delete_artifacts(
    user_id: str,
    request: Request,
    body: BulkDeleteRequest,
):
    """Bulk-delete artifacts. Each ID is verified to belong to user_id.

    Filesystem cleanup is best-effort per artifact: a failed rmtree is
    logged but the DB row is still removed (matches the per-row delete
    contract in agents_artifacts.delete_artifact and avoids leaving
    half-deleted state across many rows).
    """
    await _verify_user_self(request, user_id)
    db = await get_db_client()
    repo = ArtifactRepository(db)

    if not body.artifact_ids:
        return BulkDeleteResponse(deleted=0)

    # Filter out artifacts the user does NOT own — never let a tenant
    # delete another tenant's artifacts even by guessing IDs.
    ids_to_delete: List[str] = []
    skipped: List[str] = []
    folder_paths: List[Optional[str]] = []
    for aid in body.artifact_ids:
        art = await repo.get_by_id(aid)
        if art is None or art.user_id != user_id:
            skipped.append(aid)
            continue
        ids_to_delete.append(aid)
        folder_paths.append(
            os.path.join(
                settings.base_working_path,
                f"{art.agent_id}_{art.user_id}",
                "artifacts",
                aid,
            )
        )

    # Delete on-disk folders first so a partial DB delete doesn't leak
    # files behind. ignore_errors=True for individual rmtree calls because
    # one bad folder shouldn't block the rest of the batch.
    for path in folder_paths:
        if path and os.path.isdir(path):
            try:
                shutil.rmtree(path)
            except OSError as e:
                logger.warning(f"bulk_delete: rmtree failed for {path}: {e}")

    deleted = await repo.bulk_delete(ids_to_delete)
    return BulkDeleteResponse(deleted=deleted, skipped_not_owned=skipped)
