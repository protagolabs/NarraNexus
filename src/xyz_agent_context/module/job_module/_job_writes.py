"""
@file_name: _job_writes.py
@author:
@date: 2026-08-10
@description: Job WRITE helpers shared by the AgentDataStore seam (DirectStore),
the seam twin route, and the frontend job routes.

``update_job_from_args`` is the ~90-line job_update body (validate job_type /
trigger_config / next_run_time / status, build the ``updates`` dict, then
``JobInstanceService.update_job``) lifted into ONE place. It existed as three
drifting copies — the MCP tool, the ``/api/jobs/{job_id}`` frontend route, and
(after this migration) the agent-scoped seam route — and the ``effective_type``
/ ``compute_next_run`` ordering here is the load-bearing zombie-bug fix
(pre-open review #4): a one_off→scheduled switch with a new cron must compute
next_run against the NEW type, or the job silently never runs again. Keeping one
copy is the only safe way to not re-open that bug in one path.

Dialect-safe (JobRepository / JobInstanceService, no raw SQL). Returns the
COMPLETE result dict and never raises. Agent-scoped: a job owned by a different
agent reads as "not found" (no existence oracle — matches the route's posture,
which is SAFER than the old tool's "does not belong to agent X").
"""
from __future__ import annotations

from datetime import datetime, timezone as _dt_timezone
from typing import Optional
from zoneinfo import ZoneInfo

from loguru import logger

from xyz_agent_context.repository import JobRepository
from xyz_agent_context.schema.job_schema import JobStatus, JobType


async def update_job_from_args(
    db,
    agent_id: str,
    job_id: str,
    *,
    title: Optional[str] = None,
    description: Optional[str] = None,
    payload: Optional[str] = None,
    guidance_text: Optional[str] = None,
    trigger_config: Optional[dict] = None,
    job_type: Optional[str] = None,
    next_run_time: Optional[str] = None,
    status: Optional[str] = None,
    related_entity_id: Optional[str] = None,
) -> dict:
    """Update an existing job's fields (only the passed ones change)."""
    try:
        from xyz_agent_context.module.job_module.job_service import JobInstanceService

        job_repo = JobRepository(db)
        job = await job_repo.get_job(job_id)
        if not job or job.agent_id != agent_id:
            # A job owned by another agent reads as "not found" — no existence
            # oracle (the old MCP tool leaked "does not belong to agent X").
            return {"success": False, "job_id": job_id, "message": f"Job {job_id} not found"}

        updates: dict = {}

        # Resolve the effective job_type up front: trigger_config's
        # compute_next_run runs BEFORE the job_type branch, so reading
        # updates.get("job_type") there would see the OLD type — a
        # one_off→scheduled switch with a new cron would compute next_run as
        # one_off (→ None), silently zombifying the job (pre-open review #4).
        if job_type is not None:
            try:
                effective_type = JobType(job_type.lower())
            except ValueError:
                return {"success": False, "job_id": job_id,
                        "message": f"Invalid job_type: {job_type}. Valid: one_off, scheduled, ongoing"}
            updates["job_type"] = effective_type
        else:
            effective_type = job.job_type

        if title is not None:
            updates["title"] = title
        if description is not None:
            updates["description"] = description
        if payload is not None:
            updates["payload"] = payload
        if guidance_text:
            base_payload = updates.get("payload", job.payload) or ""
            updates["payload"] = f"{base_payload}\n\n## Manager Guidance\n{guidance_text}"
        if trigger_config is not None:
            from xyz_agent_context.schema.job_schema import TriggerConfig
            from xyz_agent_context.module.job_module._job_scheduling import compute_next_run
            from pydantic import ValidationError as _VE
            try:
                tc_model = TriggerConfig(**trigger_config)
            except _VE as ve:
                first = ve.errors()[0]
                loc = ".".join(str(p) for p in first.get("loc", ()))
                return {"success": False, "job_id": job_id,
                        "message": f"Invalid trigger_config ({loc}): {first['msg']}"}
            updates["trigger_config"] = tc_model
            nxt = compute_next_run(effective_type, tc_model)
            if nxt:
                updates["next_run_time"] = nxt.utc
                updates["next_run_at_local"] = nxt.local
                updates["next_run_tz"] = nxt.tz
            else:
                updates["next_run_time"] = None
                updates["next_run_at_local"] = None
                updates["next_run_tz"] = None
        if next_run_time is not None:
            # Atomic UTC + local-in-frozen-tz override so display and poller agree.
            try:
                next_utc = datetime.fromisoformat(next_run_time.replace("Z", "+00:00"))
                if next_utc.tzinfo is None:
                    next_utc = next_utc.replace(tzinfo=_dt_timezone.utc)
            except ValueError as e:
                return {"success": False, "job_id": job_id, "message": f"Invalid next_run_time format: {e}"}
            tz_name = (job.trigger_config.timezone if job.trigger_config else None) or "UTC"
            next_local = next_utc.astimezone(ZoneInfo(tz_name)).replace(tzinfo=None).isoformat()
            updates["next_run_time"] = next_utc
            updates["next_run_at_local"] = next_local
            updates["next_run_tz"] = tz_name
        if status is not None:
            try:
                updates["status"] = JobStatus(status.lower())
            except ValueError:
                return {"success": False, "job_id": job_id,
                        "message": f"Invalid status: {status}. Valid: active, paused, cancelled"}
        if related_entity_id is not None:
            updates["related_entity_id"] = related_entity_id

        if not updates:
            return {"success": False, "job_id": job_id, "message": "No fields to update"}

        return await JobInstanceService(db).update_job(job_id=job_id, updates=updates, agent_id=agent_id)
    except Exception as e:  # noqa: BLE001
        logger.exception(f"[job.update_job_from_args] failed: {e}")
        return {"success": False, "job_id": job_id, "message": f"Error: {e}"}
