"""
@file_name: commit.py
@author: NetMind.AI
@date: 2026-08-20
@description: The ONE commit tail for content-change commit points.

Three paths turn "the entry file's bytes changed" into registry state — a
user edit through PUT /content, a watch-page office edit, and external-edit
detection. They all end the same way: refresh the pointer row (hash / size /
updated_at, file_path UNCHANGED), append one history row, stage one
"updated" event. This module owns that tail so the three callers cannot
drift apart (the size rule or the event shape changing in one copy but not
the others is exactly the class of bug a shared owner prevents).
"""

from __future__ import annotations

import os

from xyz_agent_context.artifact._artifact_impl.notify import stage_artifact_event
from xyz_agent_context.artifact._artifact_impl.registration import (
    _dir_size,
    _record_history,
)
from xyz_agent_context.repository.artifact_repository import ArtifactRepository
from xyz_agent_context.schema.artifact_schema import Artifact
from xyz_agent_context.settings import settings
from xyz_agent_context.utils.db.database import AsyncDatabaseClient


def entry_size_bytes(abs_entry: str, file_path: str) -> int:
    """size_bytes under registration's semantics: an entry at the workspace
    top level counts the file alone; an entry in a dedicated directory counts
    the whole directory (sibling assets included)."""
    workspace_top = os.path.dirname(file_path) in ("", ".")
    if workspace_top:
        return os.path.getsize(abs_entry)
    return _dir_size(os.path.dirname(abs_entry))


async def commit_content_refresh(
    db: AsyncDatabaseClient,
    artifact: Artifact,
    *,
    new_hash: str,
    history_action: str,
    external: bool = False,
) -> Artifact:
    """Commit a content change on ``artifact``'s UNMOVED entry file.

    Refreshes hash/size/updated_at through the repository, appends one
    history row with ``history_action`` (user_edited / external_edited),
    and stages one "updated" event (``external`` marks detector-sourced
    commits). Returns the refreshed row.
    """
    repo = ArtifactRepository(db)
    abs_entry = os.path.join(settings.base_working_path, artifact.file_path)
    size_bytes = entry_size_bytes(abs_entry, artifact.file_path)

    await repo.update_pointer(
        artifact.artifact_id,
        file_path=artifact.file_path,  # a content commit never moves the pointer
        size_bytes=size_bytes,
        content_hash=new_hash,
    )
    updated = await repo.get_by_id(artifact.artifact_id)
    await _record_history(
        repo,
        artifact_id=artifact.artifact_id,
        agent_id=artifact.agent_id,
        file_path=artifact.file_path,
        size_bytes=size_bytes,
        action=history_action,
    )
    if updated is not None:
        await stage_artifact_event(db, action="updated", artifact=updated, external=external)
    return updated if updated is not None else artifact
