"""
@file_name: user_edit.py
@author: NetMind.AI
@date: 2026-08-19
@description: The user-edit save pipeline — the ONE commit path for edits made
by a human on an artifact editing surface (resident editor / md block editor /
html per-element commit).

Shape of a save (spec A §3):
  base_hash optimistic lock  →  atomic write (temp + os.replace)  →
  pointer row refresh (hash / size / updated_at; file_path UNCHANGED)  →
  history action="user_edited"  →  staged "updated" event.

The lock compares against the DISK, not the table fingerprint: disk is truth,
and an external writer may have changed the file after our last commit point.
A stale table hash must not let a save silently overwrite that external edit.
"""

from __future__ import annotations

import hashlib
import os
import tempfile

from xyz_agent_context.artifact._artifact_impl.errors import (
    ArtifactContentGone,
    ArtifactEditConflict,
    ArtifactKindMismatch,
    ArtifactNotFound,
    ArtifactTooLarge,
)
from xyz_agent_context.artifact._artifact_impl.notify import stage_artifact_event
from xyz_agent_context.artifact._artifact_impl.registration import (
    MAX_ARTIFACT_BYTES,
    _dir_size,
    _record_history,
    compute_entry_hash,
)
from xyz_agent_context.repository.artifact_repository import ArtifactRepository
from xyz_agent_context.schema.artifact_schema import Artifact
from xyz_agent_context.settings import settings
from xyz_agent_context.utils.db.database import AsyncDatabaseClient

# Kinds whose edit surface exists (kindRegistry editSurface != none/office).
# office-live goes through officecli command translation, never raw bytes;
# binary kinds have no text content to PUT.
EDITABLE_KINDS = ("text/markdown", "text/csv", "text/html")


async def save_user_content(
    db: AsyncDatabaseClient,
    *,
    agent_id: str,
    artifact_id: str,
    content: str,
    base_hash: str,
) -> Artifact:
    """Persist a user edit onto the artifact's entry file and commit it.

    Args:
        agent_id: The agent whose route the edit came through — must own the
            artifact row (agent-strict, same 404 discipline as the routes).
        content: Full new file content (editors always hold the whole text;
            anchored replacement for html happens client-side).
        base_hash: sha256 the editor's content was based on.

    Returns:
        The refreshed Artifact row.

    Raises:
        ArtifactNotFound / ArtifactKindMismatch / ArtifactTooLarge /
        ArtifactContentGone / ArtifactEditConflict(409, .current_hash).
    """
    repo = ArtifactRepository(db)
    artifact = await repo.get_by_id(artifact_id)
    if artifact is None or artifact.agent_id != agent_id:
        raise ArtifactNotFound(f"artifact not found: {artifact_id}")

    if artifact.kind not in EDITABLE_KINDS:
        raise ArtifactKindMismatch(
            f"kind {artifact.kind} has no direct-edit surface; "
            f"editable kinds: {', '.join(EDITABLE_KINDS)}"
        )

    raw = content.encode("utf-8")
    if len(raw) > MAX_ARTIFACT_BYTES:
        raise ArtifactTooLarge(
            f"content is {len(raw) / 1024 / 1024:.1f} MB "
            f"(> {MAX_ARTIFACT_BYTES / 1024 / 1024:.0f} MB max)"
        )

    abs_entry = os.path.join(settings.base_working_path, artifact.file_path)
    if not artifact.file_path or not os.path.isfile(abs_entry):
        raise ArtifactContentGone(f"entry file is gone: {artifact.file_path}")

    current_hash = compute_entry_hash(abs_entry)
    if current_hash != base_hash:
        raise ArtifactEditConflict(
            "the file changed since this editor loaded it",
            current_hash=current_hash or "",
        )

    # Atomic write: temp file in the SAME directory (os.replace must not cross
    # filesystems), then rename over the entry. Concurrent readers — the raw
    # route, officecli watch, the agent's own tools — see either the old bytes
    # or the new bytes, never a half-written file.
    entry_dir = os.path.dirname(abs_entry)
    fd, tmp_path = tempfile.mkstemp(dir=entry_dir, prefix=".narra_edit_", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, abs_entry)
    except BaseException:
        # Never leave temp droppings next to the entry — they'd show up in
        # dir listings and in the multi-file artifact's served folder.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    new_hash = hashlib.sha256(raw).hexdigest()
    # size_bytes mirrors registration's semantics: entry at the workspace top
    # level → the file alone; entry in a dedicated dir → the whole dir.
    artifact_root = entry_dir
    workspace_top = os.path.dirname(artifact.file_path) in ("", ".")
    size_bytes = len(raw) if workspace_top else _dir_size(artifact_root)

    await repo.update_pointer(
        artifact_id,
        file_path=artifact.file_path,  # a user edit never moves the pointer
        size_bytes=size_bytes,
        content_hash=new_hash,
    )
    updated = await repo.get_by_id(artifact_id)

    await _record_history(
        repo,
        artifact_id=artifact_id,
        agent_id=artifact.agent_id,
        file_path=artifact.file_path,
        size_bytes=size_bytes,
        action="user_edited",
    )
    await stage_artifact_event(db, action="updated", artifact=updated)
    return updated
