"""
@file_name: freshness.py
@author: NetMind.AI
@date: 2026-08-19
@description: External-edit detection (spec B §2) — the pull-model half of
"the entrance governs the table, not the files": anyone may write the entry
file at any time (the user in Word, another tool, a backup restore); this
module notices at the CONSUMPTION points (state-block render, tab activation)
and turns the fact into a commit point.

Detection is two-stage: an mtime fast-screen against the row's updated_at
(zero-IO, safe to run per state-block line), then a sha256 verify against the
row's content_hash — a moved mtime with identical bytes is touch/backup noise
and must NOT become a commit point (the event layer stays bounded by real
changes, per the commit-point contract).

A confirmed change commits exactly like any other edit: row hash/updated_at
refresh, history action="external_edited", staged "updated" event with
external=True → the outbox → WS → every open surface.
"""

from __future__ import annotations

import os
from typing import Literal

from xyz_agent_context.artifact._artifact_impl.notify import stage_artifact_event
from xyz_agent_context.artifact._artifact_impl.registration import (
    _dir_size,
    _record_history,
    compute_entry_hash,
)
from xyz_agent_context.repository.artifact_repository import ArtifactRepository
from xyz_agent_context.schema.artifact_schema import Artifact
from xyz_agent_context.settings import settings
from xyz_agent_context.utils.db.database import AsyncDatabaseClient

FreshnessVerdict = Literal["fresh", "external", "missing"]


def office_lock_present(abs_entry: str) -> bool:
    """True when a desktop Office app holds the document open.

    Word/PowerPoint/Excel drop a `~$<name>` owner-lock file next to the
    document while it is open. Its presence means officecli writes would race
    the desktop app's own saves — direct-edit surfaces grey out on it.
    """
    directory, name = os.path.split(abs_entry)
    return os.path.exists(os.path.join(directory, f"~${name}"))


async def refresh_external_state(
    db: AsyncDatabaseClient, artifact: Artifact
) -> FreshnessVerdict:
    """Detect (and commit) an external change to ``artifact``'s entry file.

    Returns:
        "fresh":    entry matches the last commit point (or only its mtime
                    moved — touch noise, silently ignored).
        "external": entry bytes differ — committed as an external edit.
        "missing":  entry is gone; nothing committed (heal's territory).
    """
    if not artifact.file_path:
        return "missing"
    abs_entry = os.path.join(settings.base_working_path, artifact.file_path)
    try:
        stat = os.stat(abs_entry)
    except OSError:
        return "missing"

    # Fast screen: an mtime at or before the last commit point cannot be an
    # unseen change. updated_at is tz-aware; st_mtime is a UTC epoch.
    if artifact.updated_at is not None:
        if stat.st_mtime <= artifact.updated_at.timestamp():
            return "fresh"

    new_hash = compute_entry_hash(abs_entry)
    if new_hash is None:
        return "missing"
    if new_hash == artifact.content_hash:
        return "fresh"  # touch/backup noise — not a commit point

    repo = ArtifactRepository(db)
    entry_dir = os.path.dirname(abs_entry)
    workspace_top = os.path.dirname(artifact.file_path) in ("", ".")
    size_bytes = stat.st_size if workspace_top else _dir_size(entry_dir)

    await repo.update_pointer(
        artifact.artifact_id,
        file_path=artifact.file_path,  # detection never moves the pointer
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
        action="external_edited",
    )
    if updated is not None:
        await stage_artifact_event(db, action="updated", artifact=updated, external=True)
    return "external"
