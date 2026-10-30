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

from narranexus.platform.artifact._artifact_impl.errors import (
    ArtifactContentGone,
    ArtifactEditConflict,
    ArtifactKindMismatch,
    ArtifactNotFound,
    ArtifactPathEscape,
    ArtifactTooLarge,
)
from narranexus.platform.artifact._artifact_impl.commit import commit_content_refresh
from narranexus.platform.artifact._artifact_impl.confinement import (
    confine_entry,
    sha256_of_confined,
    write_confined_atomic,
)
from narranexus.platform.artifact._artifact_impl.registration import MAX_ARTIFACT_BYTES
from narranexus.platform.repository.artifact_repository import ArtifactRepository
from narranexus.platform.schema.artifact_schema import Artifact
from narranexus.platform.settings import settings
from narranexus.platform.utils.db.database import AsyncDatabaseClient

# Kinds whose edit surface exists (kindRegistry editSurface != none/office).
# office-live goes through officecli command translation, never raw bytes;
# binary kinds have no text content to PUT.
EDITABLE_KINDS = ("text/markdown", "text/csv", "text/html")


def _confined_entry_hash(abs_entry: str, artifact: Artifact) -> str:
    """sha256 of the confined entry. Link on the path → ArtifactNotFound (the
    routes' 404 discipline); missing / not a regular file → ArtifactContentGone."""
    try:
        return sha256_of_confined(abs_entry)
    except ArtifactPathEscape:
        raise ArtifactNotFound(f"artifact not found: {artifact.artifact_id}")
    except OSError:
        raise ArtifactContentGone(f"entry file is gone: {artifact.file_path}")


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

    try:
        abs_entry = confine_entry(
            artifact.file_path, artifact.agent_id, artifact.user_id,
            settings.base_working_path, team_id=artifact.team_id,
        )
    except ArtifactPathEscape:
        # Same 404 discipline as the routes: a pointer outside the owner's
        # roots is treated as not-found, never written through.
        raise ArtifactNotFound(f"artifact not found: {artifact_id}")
    # Read and write ONLY through the confined helpers: the entry was proven
    # under the owner's roots above, but a directory on its path can be swapped
    # for a link between that check and any by-string open. A link refuses
    # (not-found, same discipline as the check); a missing/non-regular entry
    # is "gone".
    current_hash = _confined_entry_hash(abs_entry, artifact)
    if current_hash != base_hash:
        raise ArtifactEditConflict(
            "the file changed since this editor loaded it",
            current_hash=current_hash,
        )

    # Atomic write: temp file in the SAME directory (os.replace must not cross
    # filesystems), then rename over the entry — both through the parent
    # directory's fd. Concurrent readers — the raw route, officecli watch, the
    # agent's own tools — see either the old bytes or the new bytes, never a
    # half-written file; a failed write removes its temp file.
    try:
        write_confined_atomic(abs_entry, raw)
    except ArtifactPathEscape:
        raise ArtifactNotFound(f"artifact not found: {artifact_id}")

    new_hash = hashlib.sha256(raw).hexdigest()
    return await commit_content_refresh(
        db, artifact, abs_entry=abs_entry, new_hash=new_hash, history_action="user_edited"
    )


OFFICE_LIVE_KIND = "application/vnd.officecli-live"


async def commit_office_user_edit(
    db: AsyncDatabaseClient, *, agent_id: str, artifact_id: str
) -> Artifact:
    """Turn a watch-page user edit into a commit point (spec B §3.2).

    The bytes were already written by the officecli watch server (the single
    resident writer serializes them against the agent's own CLI edits); this
    only refreshes the registry: hash/size/updated_at + history
    action="user_edited" + staged "updated" event. Idempotent — an unchanged
    hash commits nothing, so a double-fired frontend callback cannot flood
    history.
    """
    repo = ArtifactRepository(db)
    artifact = await repo.get_by_id(artifact_id)
    if artifact is None or artifact.agent_id != agent_id:
        raise ArtifactNotFound(f"artifact not found: {artifact_id}")
    if artifact.kind != OFFICE_LIVE_KIND:
        raise ArtifactKindMismatch(
            f"office-edit-commit only applies to {OFFICE_LIVE_KIND}, "
            f"got {artifact.kind}"
        )
    try:
        abs_entry = confine_entry(
            artifact.file_path, artifact.agent_id, artifact.user_id,
            settings.base_working_path, team_id=artifact.team_id,
        )
    except ArtifactPathEscape:
        raise ArtifactNotFound(f"artifact not found: {artifact_id}")
    new_hash = _confined_entry_hash(abs_entry, artifact)
    if new_hash == artifact.content_hash:
        return artifact  # nothing changed — not a commit point

    return await commit_content_refresh(
        db, artifact, abs_entry=abs_entry, new_hash=new_hash, history_action="user_edited"
    )
