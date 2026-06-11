"""
@file_name: user_cascade.py
@author: NarraNexus
@date: 2026-06-11
@description: Hard cascade delete of a user and every dependent row + workspace.

Required by the external API protocol (v0.3) — `DELETE
/v1/external/agents/{a}/sessions/{s}` maps an external session_id to an
`ext_<agent>_<session>` user_id and then needs to wipe that user's
complete footprint without leaving orphan rows in any of the 12 child
tables or zombie workspace directories on disk.

Distinct from `UserRepository.delete_user` which is **soft-delete by
default** (sets `status='deleted'`) and only touches the `users` row.
This function is hard-delete + cascade and is meant for ephemeral
external users; do NOT call it on normal NarraNexus users (login,
local-default) without explicit Owner consent.

Idempotent: deleting a user_id that doesn't exist returns zero counts
without raising.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, Tuple

from loguru import logger

from xyz_agent_context.settings import settings


# Single source of truth for "every table keyed by user_id". Anytime a
# new user_id column is added to schema_registry.py, that table name
# MUST be appended here too — otherwise external session DELETE will
# silently leave orphan rows. The CI check planned for v1.5 will diff
# this list against schema_registry to catch drift.
#
# `users` itself is handled separately (last, after the cascade) so the
# parent row outlives the children for the duration of the delete.
TABLES_KEYED_BY_USER_ID: Tuple[str, ...] = (
    "events",
    "mcp_urls",
    "inbox_table",
    "module_instances",
    "instance_jobs",
    "instance_artifacts",
    "user_providers",
    "user_slots",
    "user_quotas",
    "user_notifications",
    "bundle_preflight_sessions",
    "skill_archives",
    "user_settings",
)


async def delete_user_cascade(
    user_id: str,
    db: Any,
    *,
    include_workspace: bool = True,
) -> Dict[str, int]:
    """Delete a user_id and every dependent row across the schema.

    Order:
        1. For each table in TABLES_KEYED_BY_USER_ID: DELETE WHERE user_id = ?
        2. DELETE FROM users WHERE user_id = ?  (the parent row, last)
        3. If include_workspace: remove every `{base}/{agent}_{user_id}/`
           workspace directory on disk.

    The DB deletes do NOT share a single explicit transaction — each
    `db.delete()` call uses its own auto-commit. Per-table failures are
    logged at warning level and the cascade continues; the affected
    table's count is reported as -1 in the result dict so the caller
    can surface a partial-failure warning. This is the same "best-effort
    cascade" model the Manyfold `DELETE /manyfold/agents/{id}` route
    uses (see backend/routes/manyfold_agents.py on origin/dev).

    Workspace removal runs AFTER the DB cascade completes so that a
    rmtree() permission failure never leaves the DB in a half-deleted
    state. A leftover directory is recoverable via `du`; a missing
    users row with live workspaces is not.

    Args:
        user_id: The user_id to delete (hard delete).
        db: A connected DatabaseBackend (or compatible — must expose
            `async def delete(table, filters) -> int`).
        include_workspace: If False, skip workspace dir removal. Test mode
            uses False to keep fixtures isolated.

    Returns:
        A dict mapping every touched table name to rows deleted, plus:
          - "users": 1 if the parent row existed, 0 if already gone
          - "workspace_dirs_removed": number of `*_<user_id>` dirs cleaned
          - "workspace_bytes_removed": total bytes freed from disk
        Per-table value of -1 indicates that table's DELETE raised; check
        logs for the underlying exception.
    """
    cascade: Dict[str, int] = {}

    for table_name in TABLES_KEYED_BY_USER_ID:
        try:
            cascade[table_name] = await db.delete(
                table_name, {"user_id": user_id}
            )
        except Exception as exc:
            logger.warning(
                "delete_user_cascade: table={!r} user_id={!r} DELETE failed: {}",
                table_name, user_id, exc,
            )
            cascade[table_name] = -1

    try:
        cascade["users"] = await db.delete("users", {"user_id": user_id})
    except Exception as exc:
        logger.error(
            "delete_user_cascade: parent users row DELETE failed for "
            "user_id={!r}: {}",
            user_id, exc,
        )
        cascade["users"] = -1

    if include_workspace:
        bytes_removed, dirs_removed = _remove_user_workspaces(user_id)
        cascade["workspace_dirs_removed"] = dirs_removed
        cascade["workspace_bytes_removed"] = bytes_removed
    else:
        cascade["workspace_dirs_removed"] = 0
        cascade["workspace_bytes_removed"] = 0

    logger.info(
        "delete_user_cascade: user_id={!r} done. cascade={}",
        user_id, cascade,
    )
    return cascade


def _remove_user_workspaces(user_id: str) -> Tuple[int, int]:
    """Find and remove every per-(agent, user) workspace dir on disk.

    NarraNexus stores per-(agent, user) workspaces at
    ``{base_working_path}/{agent_id}_{user_id}/`` (see
    skill_module.py:212). To find every workspace that belongs to one
    user, scan the base dir for entries whose name ends with
    ``_<user_id>``.

    Returns: (total_bytes_freed, dirs_removed_count).
    """
    base = Path(settings.base_working_path)
    if not base.exists():
        return 0, 0

    suffix = f"_{user_id}"
    total_bytes = 0
    dirs_removed = 0

    for entry in base.iterdir():
        if not entry.is_dir():
            continue
        if not entry.name.endswith(suffix):
            continue
        try:
            size = _dir_size_bytes(entry)
            shutil.rmtree(entry)
            total_bytes += size
            dirs_removed += 1
        except Exception as exc:
            logger.warning(
                "delete_user_cascade: rmtree({}) failed: {}", entry, exc
            )

    return total_bytes, dirs_removed


def _dir_size_bytes(path: Path) -> int:
    """Recursive byte size of a directory's contents (best-effort)."""
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            try:
                total += child.stat().st_size
            except OSError:
                pass
    return total
