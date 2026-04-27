"""
@file_name: one_shot_migrations.py
@author: Bin Liang
@date: 2026-04-21
@description: One-shot data migrations that run on every backend startup after
auto_migrate. Each function is idempotent (safe to call multiple times).

Spec: 2026-04-21-job-timezone-redesign
"""
from __future__ import annotations

import json
from typing import Any, Dict, TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from xyz_agent_context.utils.database import AsyncDatabaseClient


async def migrate_jobs_protocol_v2_timezone(db: "AsyncDatabaseClient") -> Dict[str, int]:
    """
    Cancel active jobs that predate the v2 timezone protocol.

    A job is "old-protocol" iff:
      - status in ('pending', 'active', 'paused'), AND
      - trigger_config JSON has no 'timezone' key.

    Idempotent: old rows become status='cancelled'; next-run fields nulled.
    Subsequent calls find no candidates.

    Returns: {"cancelled": <int>}
    """
    cancelled = 0
    for status in ("pending", "active", "paused"):
        rows = await db.get("instance_jobs", filters={"status": status})
        for row in rows:
            tc_raw = row.get("trigger_config")
            if not tc_raw:
                continue
            try:
                tc = json.loads(tc_raw) if isinstance(tc_raw, str) else tc_raw
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(tc, dict):
                continue
            if tc.get("timezone"):
                continue  # new protocol job, leave alone
            await db.update(
                "instance_jobs",
                {"job_id": row["job_id"]},
                {
                    "status": "cancelled",
                    "last_error": (
                        "Protocol migration: trigger_config schema now requires "
                        "timezone field, please recreate this job via the agent."
                    ),
                    "next_run_time": None,
                    "next_run_at_local": None,
                    "next_run_tz": None,
                },
            )
            cancelled += 1

    if cancelled:
        logger.info(f"[migration] jobs_protocol_v2_timezone cancelled={cancelled}")
    return {"cancelled": cancelled}
