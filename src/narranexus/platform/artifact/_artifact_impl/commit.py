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

It never resolves ``file_path`` itself: every caller has already confined the
entry to the owner's roots (``confinement.confine_entry``) and hands that
realpath in. A second, independent ``base + file_path`` join here would be the
same fact expressed twice — and the unconfined copy is the one that followed a
swapped-in symlink into another tenant's tree (2026-10-28).
"""

from __future__ import annotations

import os

from narranexus.platform.artifact._artifact_impl.confinement import (
    confined_file_size,
    owner_roots,
)
from narranexus.platform.artifact._artifact_impl.notify import stage_artifact_event
from narranexus.platform.artifact._artifact_impl.registration import (
    _dir_size,
    _record_history,
)
from narranexus.platform.repository.artifact_repository import ArtifactRepository
from narranexus.platform.schema.artifact_schema import Artifact
from narranexus.platform.settings import settings
from narranexus.platform.utils.db.database import AsyncDatabaseClient


def entry_size_bytes(abs_entry: str, artifact: Artifact) -> int:
    """size_bytes under registration's semantics: an entry sitting directly in
    a CONTAINER root (the owner's workspace, or the team folder) counts the
    file alone; an entry in a dedicated directory counts the whole directory
    (sibling assets included).

    "At a container root" is decided on the confined entry's real directory
    against ``owner_roots`` — the same set raw serving and registration use.
    It used to test ``dirname(file_path)`` of the BASE-relative stored pointer
    (``<user>/<agent>/x.md`` → never empty), so the top-level condition never
    matched and a top-level entry was charged the size of the whole workspace.
    """
    base = os.path.realpath(settings.base_working_path)
    roots = owner_roots(artifact.agent_id, artifact.user_id, base, team_id=artifact.team_id)
    entry_dir = os.path.dirname(abs_entry)
    if entry_dir in roots:
        return confined_file_size(abs_entry, base)
    return _dir_size(entry_dir)


async def commit_content_refresh(
    db: AsyncDatabaseClient,
    artifact: Artifact,
    *,
    abs_entry: str,
    new_hash: str,
    history_action: str,
    external: bool = False,
) -> Artifact:
    """Commit a content change on ``artifact``'s UNMOVED entry file.

    ``abs_entry`` is the entry's realpath as returned by ``confine_entry`` —
    the caller proves ownership, this function only measures and records.
    Refreshes hash/size/updated_at through the repository, appends one
    history row with ``history_action`` (user_edited / external_edited),
    and stages one "updated" event (``external`` marks detector-sourced
    commits). Returns the refreshed row.
    """
    repo = ArtifactRepository(db)
    size_bytes = entry_size_bytes(abs_entry, artifact)

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
