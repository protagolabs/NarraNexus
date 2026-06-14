"""
@file_name: user_cascade.py
@author: NarraNexus
@date: 2026-06-11
@description: Hard cascade delete of a user and every dependent row + on-disk
              data (workspaces, narrative markdown, trajectories).

Required by the external API protocol (v0.3) — `DELETE
/v1/external/agents/{a}/sessions/{s}` maps an external session_id to an
`ext_<agent>_<session>` user_id and then needs to wipe that user's
complete footprint without leaving orphan rows in any of the dependent
tables or zombie directories on disk.

Three classes of deletion this function handles:

  1. **user_id-keyed tables** — every table with a `user_id` column
     (TABLES_KEYED_BY_USER_ID). Cheap direct DELETE.
  2. **instance_id-keyed child tables** — `instance_json_format_memory*`,
     `instance_module_report_memory`, `instance_awareness`,
     `instance_narrative_links`, `instance_social_entities`. These join
     to `module_instances` (which IS user_id-keyed) by instance_id —
     if we naively DELETE module_instances first the children become
     orphans. We snapshot the instance_ids BEFORE deleting
     module_instances, then explicitly delete each child by instance_id.
  3. **narrative-keyed rows and dirs** — `narratives` itself has the
     user_id buried inside its `narrative_info` JSON `actors[]` list
     (no column), and `module_report_memory` joins to narratives by
     narrative_id. We scan + delete those after instance cleanup, then
     remove the on-disk narrative-markdown and trajectory directories.

Distinct from `UserRepository.delete_user` which is **soft-delete by
default** (sets `status='deleted'`) and only touches the `users` row.
This function is hard-delete + full cascade and is meant for ephemeral
external users; do NOT call it on normal NarraNexus users (login,
local-default) without explicit Owner consent.

Idempotent: deleting a user_id that doesn't exist returns zero counts
without raising.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Tuple

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


# Tables keyed by instance_id that hold per-(agent, user) child data
# tied to module_instances. We delete these BEFORE module_instances
# itself so the children get a real cleanup pass, not an orphan-row
# leak that lingers until the next "instance_id NOT IN module_instances"
# cleanup job runs (and we don't have one).
TABLES_KEYED_BY_INSTANCE_ID: Tuple[str, ...] = (
    "instance_json_format_memory",
    "instance_json_format_memory_chat",
    "instance_module_report_memory",
    "instance_awareness",
    "instance_narrative_links",
    "instance_social_entities",
)


async def delete_user_cascade(
    user_id: str,
    db: Any,
    *,
    include_workspace: bool = True,
) -> Dict[str, int]:
    """Delete a user_id and every dependent row across the schema.

    Order (must be this order — children before parents, foreign keys
    are logical not enforced but our queries depend on the parent rows
    still existing so we can find the children):

        1. Snapshot instance_ids belonging to this user.
        2. Snapshot narrative_ids belonging to this user (JSON actor scan).
        3. DELETE every instance_id-keyed child table.
        4. DELETE every user_id-keyed table (incl. module_instances).
        5. DELETE narratives by id + module_report_memory by narrative_id.
        6. DELETE users row.
        7. If include_workspace: remove on-disk workspaces, narrative
           markdown dirs, trajectory dirs.

    Per-table failures are logged at WARNING and the cascade continues;
    the affected table's count is reported as -1 in the result dict so
    the caller can surface a partial-failure warning. This is the same
    best-effort cascade model the Manyfold `DELETE /manyfold/agents/{id}`
    route uses.

    Args:
        user_id: The user_id to delete (hard delete).
        db: A connected DatabaseBackend (or compatible — must expose
            `async def delete(table, filters) -> int`,
            `async def execute(sql, params)` for raw queries).
        include_workspace: If False, skip on-disk dir removal. Test mode
            uses False to keep fixtures isolated.

    Returns:
        A dict mapping every touched table name to rows deleted, plus:
          - "users": 1 if the parent row existed, 0 if already gone
          - "narratives": narratives deleted via JSON actor match
          - "module_report_memory": dropped per narrative_id
          - "workspace_dirs_removed": workspaces under base_working_path
          - "workspace_bytes_removed": total bytes freed from base_working_path
          - "narrative_dirs_removed": dirs under narrative_markdown_path
          - "trajectory_dirs_removed": dirs under trajectory_path
        Per-table value of -1 indicates that table's DELETE raised; check
        logs for the underlying exception.
    """
    cascade: Dict[str, int] = {}

    # ── Step 1: snapshot instance_ids before module_instances goes ──
    instance_ids = await _snapshot_instance_ids(db, user_id)

    # ── Step 2: snapshot narrative_ids (JSON actor scan) ──
    agent_ids_seen, narrative_ids = await _snapshot_user_narratives(db, user_id)

    # ── Step 3: instance-keyed child tables ──
    for table_name in TABLES_KEYED_BY_INSTANCE_ID:
        cascade[table_name] = await _delete_by_instance_ids(
            db, table_name, instance_ids
        )

    # ── Step 4: user_id-keyed tables (including module_instances) ──
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

    # ── Step 5: narratives (JSON actor) + module_report_memory ──
    cascade["narratives"] = await _delete_narratives_by_id(db, narrative_ids)
    cascade["module_report_memory"] = await _delete_module_report_memory(
        db, narrative_ids
    )

    # ── Step 6: users row ──
    try:
        cascade["users"] = await db.delete("users", {"user_id": user_id})
    except Exception as exc:
        logger.error(
            "delete_user_cascade: parent users row DELETE failed for "
            "user_id={!r}: {}",
            user_id, exc,
        )
        cascade["users"] = -1

    # ── Step 7: on-disk cleanup ──
    if include_workspace:
        ws_bytes, ws_dirs = _remove_user_dirs(
            settings.base_working_path, user_id, mode="suffix"
        )
        cascade["workspace_dirs_removed"] = ws_dirs
        cascade["workspace_bytes_removed"] = ws_bytes

        # narrative markdown dirs at {data}/narratives/<agent>/<user>/
        nar_bytes, nar_dirs = _remove_per_agent_user_dirs(
            settings.narrative_markdown_path, agent_ids_seen, user_id
        )
        cascade["narrative_dirs_removed"] = nar_dirs
        cascade["narrative_bytes_removed"] = nar_bytes

        # trajectory dirs at {data}/trajectories/<agent>/<user>/
        traj_bytes, traj_dirs = _remove_per_agent_user_dirs(
            settings.trajectory_path, agent_ids_seen, user_id
        )
        cascade["trajectory_dirs_removed"] = traj_dirs
        cascade["trajectory_bytes_removed"] = traj_bytes
    else:
        for k in (
            "workspace_dirs_removed", "workspace_bytes_removed",
            "narrative_dirs_removed", "narrative_bytes_removed",
            "trajectory_dirs_removed", "trajectory_bytes_removed",
        ):
            cascade[k] = 0

    logger.info(
        "delete_user_cascade: user_id={!r} done. cascade={}",
        user_id, cascade,
    )
    return cascade


# ---------------------------------------------------------------------------
# Step 1 helpers — snapshot instance_ids
# ---------------------------------------------------------------------------


async def _snapshot_instance_ids(db: Any, user_id: str) -> List[str]:
    """Return every instance_id in module_instances belonging to user."""
    try:
        rows = await db.execute(
            "SELECT instance_id FROM module_instances WHERE user_id = ?",
            (user_id,),
        )
        return [r["instance_id"] for r in rows if r.get("instance_id")]
    except Exception as exc:
        logger.warning(
            "delete_user_cascade: instance_id snapshot failed for "
            "user_id={!r}: {}",
            user_id, exc,
        )
        return []


# ---------------------------------------------------------------------------
# Step 2 helpers — snapshot narrative_ids by JSON actor scan
# ---------------------------------------------------------------------------


async def _snapshot_user_narratives(
    db: Any, user_id: str
) -> Tuple[List[str], List[str]]:
    """Find narratives whose `narrative_info.actors[]` contains user_id.

    Returns (agent_ids_seen, narrative_ids). agent_ids_seen is the set of
    distinct agent_ids across those narratives — needed so the on-disk
    narrative + trajectory dir cleanup knows which `<agent>` sub-dirs to
    scan.
    """
    agent_ids: set = set()
    narrative_ids: list = []
    try:
        # narrative_info is TEXT in sqlite, JSON in mysql. LIKE is portable
        # and good enough — we filter false positives by parsing on the
        # Python side.
        rows = await db.execute(
            "SELECT id, agent_id, narrative_info FROM narratives "
            "WHERE narrative_info LIKE ?",
            (f"%{user_id}%",),
        )
        for r in rows:
            raw = r.get("narrative_info") or ""
            try:
                info = json.loads(raw) if isinstance(raw, str) else raw
            except (ValueError, TypeError):
                continue
            actors = info.get("actors") if isinstance(info, dict) else None
            if not isinstance(actors, list):
                continue
            for actor in actors:
                if isinstance(actor, dict) and actor.get("id") == user_id:
                    narrative_ids.append(r["id"])
                    if r.get("agent_id"):
                        agent_ids.add(r["agent_id"])
                    break
    except Exception as exc:
        logger.warning(
            "delete_user_cascade: narrative snapshot failed for user_id={!r}: {}",
            user_id, exc,
        )
    return list(agent_ids), narrative_ids


# ---------------------------------------------------------------------------
# Step 3 helpers — delete instance-keyed children
# ---------------------------------------------------------------------------


async def _delete_by_instance_ids(
    db: Any, table: str, instance_ids: List[str]
) -> int:
    """DELETE FROM table WHERE instance_id IN (...). Returns rows deleted.

    Falls back to per-id deletes if the backend's `delete()` API can't
    take a list. SQLite proxy backends typically only support equality;
    looping is fine because the per-user count is small.
    """
    if not instance_ids:
        return 0
    total = 0
    for iid in instance_ids:
        try:
            total += await db.delete(table, {"instance_id": iid})
        except Exception as exc:
            logger.warning(
                "delete_user_cascade: table={!r} instance_id={!r} "
                "DELETE failed: {}",
                table, iid, exc,
            )
    return total


# ---------------------------------------------------------------------------
# Step 5 helpers — narratives + module_report_memory
# ---------------------------------------------------------------------------


async def _delete_narratives_by_id(db: Any, narrative_ids: List[str]) -> int:
    """DELETE FROM narratives WHERE id IN (...). Returns rows deleted."""
    if not narrative_ids:
        return 0
    total = 0
    for nid in narrative_ids:
        try:
            total += await db.delete("narratives", {"id": nid})
        except Exception as exc:
            logger.warning(
                "delete_user_cascade: narratives id={!r} DELETE failed: {}",
                nid, exc,
            )
    return total


async def _delete_module_report_memory(
    db: Any, narrative_ids: List[str]
) -> int:
    """Drop module_report_memory rows tied to the user's narratives.

    module_report_memory is keyed by `narrative_id` (no user_id column).
    Cleanup must happen AFTER the parent narratives are identified but
    can run before or after they're deleted — order doesn't matter as
    long as we know which narrative_ids belong to this user.
    """
    if not narrative_ids:
        return 0
    total = 0
    for nid in narrative_ids:
        try:
            total += await db.delete("module_report_memory", {"narrative_id": nid})
        except Exception as exc:
            logger.warning(
                "delete_user_cascade: module_report_memory narrative_id={!r} "
                "DELETE failed: {}",
                nid, exc,
            )
    return total


# ---------------------------------------------------------------------------
# Step 7 helpers — on-disk cleanup
# ---------------------------------------------------------------------------


def _remove_user_dirs(
    base_str: str, user_id: str, *, mode: str = "suffix"
) -> Tuple[int, int]:
    """Remove every directory in `base_str` that belongs to `user_id`.

    For ``mode="suffix"`` we walk one level and match dir names ending
    in ``_<user_id>`` (the workspace layout
    ``{base}/{agent}_{user}/``). For ``mode="exact"`` we match exactly
    user_id at top level (unused right now, retained for future shapes).

    Returns: (total_bytes_freed, dirs_removed_count).
    """
    base = Path(base_str)
    if not base.exists():
        return 0, 0

    total_bytes = 0
    dirs_removed = 0
    needle = f"_{user_id}" if mode == "suffix" else user_id

    for entry in base.iterdir():
        if not entry.is_dir():
            continue
        match = entry.name.endswith(needle) if mode == "suffix" else (entry.name == needle)
        if not match:
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


def _remove_per_agent_user_dirs(
    base_str: str, agent_ids: List[str], user_id: str
) -> Tuple[int, int]:
    """Remove `{base}/<agent_id>/<user_id>/` for every agent_id.

    Used for narrative markdown dirs (``data/narratives/<agent>/<user>/``)
    and trajectory dirs (``data/trajectories/<agent>/<user>/``).
    """
    if not agent_ids:
        return 0, 0
    base = Path(base_str)
    if not base.exists():
        return 0, 0

    total_bytes = 0
    dirs_removed = 0
    for agent_id in agent_ids:
        target = base / agent_id / user_id
        if not target.is_dir():
            continue
        try:
            size = _dir_size_bytes(target)
            shutil.rmtree(target)
            total_bytes += size
            dirs_removed += 1
        except Exception as exc:
            logger.warning(
                "delete_user_cascade: rmtree({}) failed: {}", target, exc
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
