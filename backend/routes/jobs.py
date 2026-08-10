"""
@file_name: jobs.py
@author: NetMind.AI
@date: 2025-11-28
@description: REST API routes for jobs

Provides endpoints for:
- GET /api/jobs - List jobs for an agent/user
- GET /api/jobs/{job_id} - Get job details
- PUT /api/jobs/{job_id} - Update job fields (mirrors job_update MCP tool)
- PUT /api/jobs/{job_id}/cancel - Cancel a job
- PUT /api/jobs/{job_id}/pause - Pause a job (mirrors job_pause MCP tool)
- GET /api/jobs/search/semantic - BM25 search (mirrors job_retrieval_semantic MCP tool)
- GET /api/jobs/search/keywords - Keyword search (mirrors job_retrieval_by_keywords MCP tool)
- POST /api/jobs/complex - Create batch jobs with dependencies (Job Complex)

Refactoring notes (2025-12-24):
- Retrieve data from instance_jobs table

Refactoring notes (2026-01-04):
- Added Job Complex batch creation API

Refactoring notes (2026-08-10):
- Added update/pause/search-semantic/search-keywords — the backend half of the
  MCP data-access seam. Each mirrors the matching tool in
  src/xyz_agent_context/module/job_module/_job_mcp_tools.py exactly (same
  repository/service calls, same response shape) so a non-agent caller (e.g. a
  frontend panel) gets identical semantics to the agent's own tools. Gated by
  `assert_owned` — the dashboard route's pause/resume (job_recovery, status
  preconditioned) is a DIFFERENT, unrelated code path; see this file's mirror
  doc for why both exist.
"""

import json
from typing import Optional, Any, List
from uuid import uuid4
from pydantic import BaseModel
from fastapi import APIRouter, Query, Request
from loguru import logger

from backend.auth import resolve_current_user_id
from backend.routes._ownership import assert_owned
from xyz_agent_context.utils.db.db_factory import get_db_client
from xyz_agent_context.utils import format_for_api
from xyz_agent_context.repository import JobRepository
from xyz_agent_context.module.job_module import (
    # aliased: the route handlers below share these names
    search_jobs_semantic as _shared_search_semantic,
    search_jobs_by_keywords as _shared_search_keywords,
)
from xyz_agent_context.schema import (
    JobStatus,
    JobType,
    JobResponse,
    JobListResponse,
    JobDetailResponse,
    TriggerConfig,
)


class CancelJobResponse(BaseModel):
    """Response model for cancel job"""
    success: bool
    job_id: Optional[str] = None
    previous_status: Optional[str] = None
    error: Optional[str] = None


class JobUpdateBody(BaseModel):
    """Update request — mirrors job_update MCP tool args. Only passed fields change."""
    agent_id: str
    title: Optional[str] = None
    description: Optional[str] = None
    payload: Optional[str] = None
    guidance_text: Optional[str] = None
    trigger_config: Optional[dict] = None
    job_type: Optional[str] = None
    next_run_time: Optional[str] = None
    status: Optional[str] = None
    related_entity_id: Optional[str] = None


class JobUpdateResponse(BaseModel):
    """Response model for job update — same shape as JobInstanceService.update_job()."""
    success: bool
    job_id: Optional[str] = None
    updated_fields: List[str] = []
    message: Optional[str] = None


class JobPauseBody(BaseModel):
    """Pause request — mirrors job_pause MCP tool args."""
    agent_id: str


class JobPauseResponse(BaseModel):
    """Response model for job pause — same shape as the job_pause MCP tool."""
    success: bool
    job_id: str
    status: Optional[str] = None
    message: Optional[str] = None


class JobSemanticSearchResponse(BaseModel):
    """Response model for semantic (BM25) job search — same shape as job_retrieval_semantic."""
    success: bool
    query: Optional[str] = None
    total_results: int = 0
    jobs: List[dict] = []
    error: Optional[str] = None


class JobKeywordSearchResponse(BaseModel):
    """Response model for keyword job search — same shape as job_retrieval_by_keywords."""
    success: bool
    keywords: List[str] = []
    total_results: int = 0
    jobs: List[dict] = []
    error: Optional[str] = None


class JobComplexJobRequest(BaseModel):
    """Creation request for a single Job"""
    task_key: str  # Task identifier (used for dependency references)
    title: str
    description: Optional[str] = None
    depends_on: List[str] = []  # List of dependent task_keys
    payload: Optional[str] = None


class CreateJobComplexRequest(BaseModel):
    """Request to create a Job Complex"""
    agent_id: str
    user_id: str
    group_id: Optional[str] = None  # Optional group ID
    jobs: List[JobComplexJobRequest]


class CreateJobComplexResponse(BaseModel):
    """Response for creating a Job Complex"""
    success: bool
    group_id: Optional[str] = None
    jobs_created: int = 0
    job_ids: List[str] = []
    error: Optional[str] = None


router = APIRouter()


def _parse_json(value: Any, default: Any) -> Any:
    """Parse JSON field"""
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value


def job_row_to_response(row: dict, depends_on: List[str] = None) -> JobResponse:
    """
    Convert instance_jobs row to JobResponse

    Args:
        row: Database row data
        depends_on: List of dependent instance_ids (retrieved from module_instances table)
    """
    # Parse JSON fields
    trigger_config_raw = row.get("trigger_config")
    process_raw = row.get("process")

    # Recursively parse JSON (handle double-encoding issues)
    def parse_json_recursive(value, expected_type, default):
        """Recursively parse JSON until the expected type is obtained"""
        if isinstance(value, expected_type):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                # Continue recursive parsing
                return parse_json_recursive(parsed, expected_type, default)
            except (json.JSONDecodeError, TypeError):
                return default
        return default

    trigger_config = parse_json_recursive(trigger_config_raw, dict, {})
    process = parse_json_recursive(process_raw, list, [])

    return JobResponse(
        job_id=row.get("job_id"),
        agent_id=row.get("agent_id"),
        user_id=row.get("user_id"),
        job_type=row.get("job_type", "one_off"),
        title=row.get("title", ""),
        description=row.get("description", ""),
        status=row.get("status", "pending"),
        payload=row.get("payload"),
        trigger_config=trigger_config,
        process=process,
        # v2: expose user-local beta fields only; frontend renders them verbatim
        # (no Date() coercion) so the timezone label shown matches the job's
        # frozen timezone regardless of the viewer's browser timezone.
        next_run_at=row.get("next_run_at_local"),
        next_run_timezone=row.get("next_run_tz"),
        last_run_at=row.get("last_run_at_local"),
        last_run_timezone=row.get("last_run_tz"),
        last_error=row.get("last_error"),
        notification_method=row.get("notification_method"),
        created_at=format_for_api(row.get("created_at")),
        updated_at=format_for_api(row.get("updated_at")),
        # New fields
        instance_id=row.get("instance_id"),
        depends_on=depends_on or [],
        # Surface narrative_id so the bundle export wizard (P7) can group
        # jobs under their parent narrative for visual selection.
        narrative_id=row.get("narrative_id"),
    )


@router.get("", response_model=JobListResponse)
async def list_jobs(
    request: Request,
    agent_id: str = Query(..., description="Agent ID"),
    status: Optional[str] = Query(None, description="Optional status filter"),
    limit: int = Query(50, description="Max number of jobs to return"),
):
    """
    List jobs for an agent. Identity from auth_middleware — the previous
    "optional user_id filter" let any client list anyone's jobs by
    passing a different user_id in the URL.

    Retrieves data from instance_jobs table and dependency relationships from module_instances table
    """
    user_id = await resolve_current_user_id(request)
    logger.debug(f"Listing jobs for agent: {agent_id}, user: {user_id}, status: {status}")

    try:
        db_client = await get_db_client()

        # Build filter conditions — always scoped to the caller.
        filters = {"agent_id": agent_id, "user_id": user_id}
        if status:
            # Validate status value (derive from the enum so new states —
            # paused_no_quota / cooling / blocked / blocked_failed / paused —
            # are accepted automatically).
            valid_statuses = [s.value for s in JobStatus]
            if status not in valid_statuses:
                return JobListResponse(
                    success=False,
                    error=f"Invalid status: {status}. Valid values: {valid_statuses}"
                )
            filters["status"] = status

        # Get data from instance_jobs table
        jobs_data = await db_client.get(
            "instance_jobs",
            filters=filters,
            order_by="created_at DESC",
            limit=limit
        )

        # Collect all instance_ids, batch query dependency relationships
        instance_ids = [row.get("instance_id") for row in jobs_data if row.get("instance_id")]

        # Batch fetch dependency relationships from module_instances table (using get_by_ids to avoid IN query issues)
        instance_deps_map: dict[str, List[str]] = {}
        if instance_ids:
            instances_data = await db_client.get_by_ids(
                "module_instances",
                "instance_id",
                instance_ids
            )
            for inst in instances_data:
                inst_id = inst.get("instance_id")
                deps_raw = inst.get("dependencies")
                # Parse dependencies (may be JSON string or list)
                if deps_raw:
                    if isinstance(deps_raw, str):
                        try:
                            deps = json.loads(deps_raw)
                        except json.JSONDecodeError:
                            deps = []
                    elif isinstance(deps_raw, list):
                        deps = deps_raw
                    else:
                        deps = []
                    instance_deps_map[inst_id] = deps

        # Convert to response format (including dependency relationships)
        job_responses = []
        for row in jobs_data:
            instance_id = row.get("instance_id")
            depends_on = instance_deps_map.get(instance_id, [])
            job_responses.append(job_row_to_response(row, depends_on))

        logger.debug(f"Found {len(job_responses)} jobs")

        return JobListResponse(
            success=True,
            jobs=job_responses,
            count=len(job_responses),
        )

    except Exception as e:
        logger.exception(f"Error listing jobs: {e}")
        return JobListResponse(
            success=False,
            error=str(e)
        )


@router.get("/{job_id}", response_model=JobDetailResponse)
async def get_job_details(job_id: str):
    """
    Get job details by ID

    Retrieves data from instance_jobs table
    """
    logger.info(f"Getting job details: {job_id}")

    try:
        db_client = await get_db_client()

        # Get data from instance_jobs table
        job_data = await db_client.get_one(
            "instance_jobs",
            filters={"job_id": job_id}
        )

        if job_data:
            return JobDetailResponse(
                success=True,
                job=job_row_to_response(job_data),
            )
        else:
            return JobDetailResponse(
                success=False,
                error=f"Job not found: {job_id}"
            )

    except Exception as e:
        logger.exception(f"Error getting job details: {e}")
        return JobDetailResponse(
            success=False,
            error=str(e)
        )


@router.put("/{job_id}/cancel", response_model=CancelJobResponse)
async def cancel_job(job_id: str):
    """
    Cancel a Job

    Sets the Job status to cancelled so it will no longer be polled for execution by JobTrigger.
    Only Jobs in pending or active status can be cancelled.
    Jobs in running status cannot be interrupted, but will be marked as cancelled and will not be re-executed.
    """
    logger.info(f"Cancel job request: {job_id}")

    try:
        db_client = await get_db_client()
        job_repo = JobRepository(db_client)

        # Get current Job status
        job = await job_repo.get_job(job_id)
        if not job:
            return CancelJobResponse(
                success=False,
                error=f"Job not found: {job_id}"
            )

        previous_status = job.status.value

        # Check if cancellation is possible
        if job.status in (JobStatus.COMPLETED, JobStatus.CANCELLED):
            return CancelJobResponse(
                success=False,
                job_id=job_id,
                previous_status=previous_status,
                error=f"Job is already {previous_status}, cannot cancel"
            )

        # Update status to cancelled
        await job_repo.update_job_status(job_id, JobStatus.CANCELLED)

        logger.info(f"Job {job_id} cancelled successfully (was: {previous_status})")

        return CancelJobResponse(
            success=True,
            job_id=job_id,
            previous_status=previous_status,
        )

    except Exception as e:
        logger.exception(f"Error cancelling job: {e}")
        return CancelJobResponse(
            success=False,
            error=str(e)
        )


@router.post("/complex", response_model=CreateJobComplexResponse)
async def create_job_complex(request: CreateJobComplexRequest):
    """
    Batch create a group of Jobs with dependency relationships (Job Complex)

    Workflow:
    1. Validate dependencies (ensure all task_keys referenced in depends_on exist)
    2. Topological sort to determine creation order
    3. Batch create Jobs, mapping task_key to actual job_id
    4. Root Jobs (no dependencies) set to ACTIVE, dependent Jobs set to PENDING

    Dependency relationships are stored in the depends_on field within payload
    """
    logger.info(f"Creating Job Complex: {len(request.jobs)} jobs")

    try:
        # 1. Validate dependencies
        task_keys = {job.task_key for job in request.jobs}
        for job in request.jobs:
            for dep in job.depends_on:
                if dep not in task_keys:
                    return CreateJobComplexResponse(
                        success=False,
                        error=f"Invalid dependency: '{dep}' not found in job list"
                    )

        # 2. Generate group_id
        group_id = request.group_id or f"group_{uuid4().hex[:8]}"

        # 3. Create Jobs via JobInstanceService (creates ModuleInstance + Job records)
        db_client = await get_db_client()
        from xyz_agent_context.module.job_module.job_service import JobInstanceService
        job_service = JobInstanceService(db_client)

        job_ids = []
        task_key_to_job_id = {}  # task_key -> job_id mapping

        for job in request.jobs:
            # Convert task_key dependencies to job_id dependencies
            depends_on_job_ids = [task_key_to_job_id[dep] for dep in job.depends_on]

            # Build payload with dependency information
            payload_str = json.dumps({
                "task_key": job.task_key,
                "depends_on": depends_on_job_ids,
                "group_id": group_id,
                "original_payload": job.payload,
            })

            result = await job_service.create_job_with_instance(
                agent_id=request.agent_id,
                user_id=request.user_id,
                title=job.title,
                description=job.description or "",
                job_type="one_off",
                trigger_config=TriggerConfig.immediate().model_dump(mode="json"),
                payload=payload_str,
                dependencies=depends_on_job_ids if depends_on_job_ids else None,
                # Deterministic batch with caller-chosen titles: sibling jobs in
                # one group are often near-duplicates by title ("X part 1/2") —
                # the LLM-repeat similarity gate must not merge or block them.
                confirm_new=True,
            )

            if not result.get("success"):
                error_msg = result.get("error", "Unknown error creating job")
                logger.error(f"Failed to create job for task_key={job.task_key}: {error_msg}")
                return CreateJobComplexResponse(
                    success=False,
                    error=f"Failed to create job '{job.title}': {error_msg}"
                )

            job_id = result.get("job_id", "")
            task_key_to_job_id[job.task_key] = job_id
            job_ids.append(job_id)
            logger.info(f"Created job: {job_id} (task_key: {job.task_key}, result: {result})")

        logger.info(f"Job Complex created: group_id={group_id}, {len(job_ids)} jobs")

        return CreateJobComplexResponse(
            success=True,
            group_id=group_id,
            jobs_created=len(job_ids),
            job_ids=job_ids,
        )

    except Exception as e:
        logger.exception(f"Error creating Job Complex: {e}")
        return CreateJobComplexResponse(
            success=False,
            error=str(e)
        )


@router.put("/{job_id}", response_model=JobUpdateResponse)
async def update_job(job_id: str, request: Request, body: JobUpdateBody):
    """
    Update Job fields — mirrors the `job_update` MCP tool
    (src/xyz_agent_context/module/job_module/_job_mcp_tools.py L410).
    Only passed fields change.
    """
    await assert_owned(request, body.agent_id)

    try:
        from datetime import datetime, timezone as dt_timezone
        from zoneinfo import ZoneInfo
        from xyz_agent_context.module.job_module.job_service import JobInstanceService

        db_client = await get_db_client()
        job_repo = JobRepository(db_client)

        job = await job_repo.get_job(job_id)
        if not job:
            return JobUpdateResponse(success=False, job_id=job_id, message=f"Job {job_id} not found")

        if job.agent_id != body.agent_id:
            return JobUpdateResponse(
                success=False,
                job_id=job_id,
                message=f"Job {job_id} not found",
            )

        updates: dict = {}

        # Resolve the effective job_type up front: trigger_config's
        # compute_next_run runs BEFORE the job_type branch below, so reading
        # updates.get("job_type") there always saw the OLD type — a
        # one_off→scheduled switch with a new cron computed next_run_time as
        # one_off (→ None), silently zombifying the job (pre-open review #4).
        if body.job_type is not None:
            try:
                effective_type = JobType(body.job_type.lower())
            except ValueError:
                return JobUpdateResponse(
                    success=False,
                    job_id=job_id,
                    message=f"Invalid job_type: {body.job_type}. Valid: one_off, scheduled, ongoing",
                )
            updates["job_type"] = effective_type
        else:
            effective_type = job.job_type

        if body.title is not None:
            updates["title"] = body.title
        if body.description is not None:
            updates["description"] = body.description
        if body.payload is not None:
            updates["payload"] = body.payload
        if body.guidance_text:
            base_payload = updates.get("payload", job.payload) or ""
            updates["payload"] = f"{base_payload}\n\n## Manager Guidance\n{body.guidance_text}"
        if body.trigger_config is not None:
            from xyz_agent_context.module.job_module._job_scheduling import compute_next_run
            from pydantic import ValidationError as _VE
            try:
                tc_model = TriggerConfig(**body.trigger_config)
            except _VE as ve:
                first = ve.errors()[0]
                loc = ".".join(str(p) for p in first.get("loc", ()))
                return JobUpdateResponse(
                    success=False, job_id=job_id, message=f"Invalid trigger_config ({loc}): {first['msg']}"
                )
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
        if body.next_run_time is not None:
            # Atomic alpha+beta override: parse UTC input, then derive the beta
            # pair in the job's frozen timezone so display and poller stay consistent.
            try:
                next_utc = datetime.fromisoformat(body.next_run_time.replace("Z", "+00:00"))
                if next_utc.tzinfo is None:
                    next_utc = next_utc.replace(tzinfo=dt_timezone.utc)
            except ValueError as e:
                return JobUpdateResponse(
                    success=False, job_id=job_id, message=f"Invalid next_run_time format: {e}"
                )
            tz_name = (job.trigger_config.timezone if job.trigger_config else None) or "UTC"
            next_local = next_utc.astimezone(ZoneInfo(tz_name)).replace(tzinfo=None).isoformat()
            updates["next_run_time"] = next_utc
            updates["next_run_at_local"] = next_local
            updates["next_run_tz"] = tz_name
        if body.status is not None:
            try:
                updates["status"] = JobStatus(body.status.lower())
            except ValueError:
                return JobUpdateResponse(
                    success=False,
                    job_id=job_id,
                    message=f"Invalid status: {body.status}. Valid: active, paused, cancelled",
                )
        if body.related_entity_id is not None:
            updates["related_entity_id"] = body.related_entity_id

        if not updates:
            return JobUpdateResponse(success=False, job_id=job_id, message="No fields to update")

        service = JobInstanceService(db_client)
        result = await service.update_job(job_id=job_id, updates=updates, agent_id=body.agent_id)
        return JobUpdateResponse(**result)

    except Exception as e:
        logger.exception(f"Error updating job {job_id}: {e}")
        return JobUpdateResponse(success=False, job_id=job_id, message=str(e))


@router.put("/{job_id}/pause", response_model=JobPauseResponse)
async def pause_job(job_id: str, request: Request, body: JobPauseBody):
    """
    Pause a Job — mirrors the `job_pause` MCP tool
    (src/xyz_agent_context/module/job_module/_job_mcp_tools.py L553).

    Unconditional: sets status to PAUSED regardless of the current status (no
    precondition check). This differs from the dashboard route's
    `/api/dashboard/jobs/{id}/pause`, which goes through
    `job_recovery.pause_job` and only allows pausing from active/pending —
    that route exists for the human-facing dashboard; this one exists so a
    non-agent caller gets the exact same semantics the agent's own
    `job_pause` tool has.
    """
    await assert_owned(request, body.agent_id)

    try:
        db_client = await get_db_client()
        job_repo = JobRepository(db_client)

        job = await job_repo.get_job(job_id)
        if not job:
            return JobPauseResponse(success=False, job_id=job_id, message=f"Job {job_id} not found")
        if job.agent_id != body.agent_id:
            return JobPauseResponse(
                success=False, job_id=job_id, message=f"Job {job_id} not found"
            )

        updated_rows = await job_repo.pause_job(job_id)

        return JobPauseResponse(
            success=updated_rows > 0,
            job_id=job_id,
            status="paused",
            message="Job paused successfully" if updated_rows > 0 else "Failed to pause job",
        )

    except Exception as e:
        logger.exception(f"Error pausing job {job_id}: {e}")
        return JobPauseResponse(success=False, job_id=job_id, message=str(e))


@router.get("/search/semantic", response_model=JobSemanticSearchResponse)
async def search_jobs_semantic(
    request: Request,
    agent_id: str = Query(..., description="Agent ID"),
    query: str = Query(..., min_length=1, max_length=512, description="Natural language search query"),
    status: Optional[str] = Query(None, description="Optional status filter"),
    limit: int = Query(10, ge=1, le=100, description="Max number of results"),
):
    """
    Search jobs by relevance to a natural-language query — the frontend-facing
    twin of the `job_retrieval_semantic` MCP tool. Both this route and the
    MCP-tool/seam path now call the ONE shared implementation
    (`xyz_agent_context.module.job_module.search_jobs_semantic`), so the job read
    semantics can't drift between the browser API and the agent path.

    Despite the name, this is BM25 keyword ranking, not vector cosine similarity
    — vectors were retired from job search; the tool kept its name for
    LLM-facing continuity.
    """
    await assert_owned(request, agent_id)

    # Same user-scoping decision as list_jobs above: the caller's own identity
    # is the filter — an "optional user_id" query param let any client read
    # anyone's jobs, and these endpoints must not reintroduce what that fix
    # removed (jobs are per-user records under the agent). This is deliberately
    # STRICTER than the agent-path seam route (agents/jobs.py), which trusts the
    # agent to pass a user_id (it queries its OWN agent's jobs).
    user_id = await resolve_current_user_id(request)
    result = await _shared_search_semantic(
        await get_db_client(), agent_id, query, user_id, status, limit,
    )
    return JobSemanticSearchResponse(**result)


@router.get("/search/keywords", response_model=JobKeywordSearchResponse)
async def search_jobs_by_keywords(
    request: Request,
    agent_id: str = Query(..., description="Agent ID"),
    keywords: List[str] = Query(..., min_length=1, max_length=20, description="Keywords to search for (matches if ANY keyword found)"),
    status: Optional[str] = Query(None, description="Optional status filter"),
    limit: int = Query(20, ge=1, le=100, description="Max number of results"),
):
    """
    Search jobs by keyword matching — the frontend-facing twin of the
    `job_retrieval_by_keywords` MCP tool. Shares the ONE implementation
    (`xyz_agent_context.module.job_module.search_jobs_by_keywords`) with the
    agent path — see search_jobs_semantic above for the user-scoping rationale.
    """
    await assert_owned(request, agent_id)

    # Caller's own identity is the filter (stricter than the agent-path seam
    # route — see search_jobs_semantic above).
    user_id = await resolve_current_user_id(request)
    result = await _shared_search_keywords(
        await get_db_client(), agent_id, keywords, user_id, status, limit,
    )
    return JobKeywordSearchResponse(**result)
