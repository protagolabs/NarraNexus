"""
Job Module - Background Task Management

@file_name: job_module.py
@author: NetMind.AI
@date: 2025-11-25
@description: Provides background task capabilities for Agent

=============================================================================
Module Overview
=============================================================================

JobModule enables Agents to create and manage background tasks that execute
asynchronously without blocking user interactions.

Capabilities:
1. **Instructions** - Guide Agent on when/how to create Jobs
2. **Tools (MCP)** - job_create, job_retrieval_semantic, job_retrieval_by_id, job_retrieval_by_keywords
3. **Data** - Job history stored in job_table
4. **Hooks**:
    - hook_data_gathering: Load user's active jobs for context
    - hook_after_event_execution: LLM-powered job status update after execution

Use Cases:
- Scheduled tasks: "Every day at 8am..."
- Recurring tasks: "Weekly summary every Friday..."
- Delayed tasks: "Remind me in 1 hour..."

Architecture:
    ┌─────────────────────────────────────────────────────────────┐
    │                         JobModule                            │
    ├─────────────────────────────────────────────────────────────┤
    │  MCP Tools:                                                  │
    │    job_create, job_retrieval_semantic,                       │
    │    job_retrieval_by_id, job_retrieval_by_keywords            │
    ├─────────────────────────────────────────────────────────────┤
    │  Hooks:                                                      │
    │    hook_data_gathering → Load active jobs to instructions    │
    │    hook_after_event_execution → LLM analyze & update status  │
    └─────────────────────────────────────────────────────────────┘
                                │
                                ▼
    ┌─────────────────────────────────────────────────────────────┐
    │               create_instance_jobs_table.py                   │
    │                    TableManager definition                    │
    └─────────────────────────────────────────────────────────────┘

Related:
- job_trigger.py: Background polling service that triggers job execution
- job_schema.py: Data models (JobModel, JobExecutionResult, TriggerConfig)
"""


import json
from typing import Optional, List, Dict, Any

from loguru import logger
from fastmcp import Client

# Module (same package)
from xyz_agent_context.module import XYZBaseModule

from xyz_agent_context.agent_framework.openai_agents_sdk import OpenAIAgentsSDK

# Schema
from xyz_agent_context.schema import (
    ModuleConfig,
    MCPServerConfig,
    ContextData,
    HookAfterExecutionParams,
    WorkingSource,
)
from xyz_agent_context.schema.module_schema import (
    HookCallbackResult,
    InstanceStatus,
)
from xyz_agent_context.schema.job_schema import (
    JobType,
    JobStatus,
    JobModel,
    TriggerConfig,
    JobExecutionResult,
    OngoingExecutionResult,
)

# Utils
from xyz_agent_context.utils import DatabaseClient, get_db_client, utc_now
from xyz_agent_context.utils.embedding import get_embedding, prepare_job_text_for_embedding

from datetime import datetime
from uuid import uuid4

# Repository
from xyz_agent_context.repository import JobRepository
from xyz_agent_context.repository.job_repository import format_jobs_for_display, calculate_next_run_time

# Module Instance Factory
from xyz_agent_context.module._module_impl.instance_factory import InstanceFactory

# Extracted sub-modules
from xyz_agent_context.module.job_module._job_analysis import (
    extract_execution_trace,
    extract_context_info,
    build_job_analysis_prompt,
)
from xyz_agent_context.module.job_module._job_mcp_tools import create_job_mcp_server


class JobModule(XYZBaseModule):
    """
    Job Module - Background task management module

    Provides background task capabilities:
    1. **Instructions** - Tells Agent when to create Jobs and how to fill in parameters
    2. **Tools (MCP)** - Provides job_create, job_retrieval_* and other tools
    3. **Data** - Jobs stored in job_table
    4. **Hooks**:
        - hook_data_gathering: Load user's active Job list into instructions
        - hook_after_event_execution: After Job execution, use LLM to analyze results and update status

    Collaboration with JobTrigger:
    - JobTrigger is responsible for polling and triggering Job execution
    - JobModule is responsible for creating Jobs and post-execution status updates
    """

    # =========================================================================
    # Initialization
    # =========================================================================

    def __init__(
        self,
        agent_id: str,
        user_id: Optional[str] = None,
        database_client: Optional[DatabaseClient] = None,
        instance_id: Optional[str] = None,
        instance_ids: Optional[List[str]] = None
    ):
        """
        Initialize JobModule

        Args:
            agent_id: Agent ID, used for data isolation
            user_id: User ID, the user who owns the Job
            database_client: Database client
            instance_id: Instance ID (if provided, indicates this is a specific instance operation)
            instance_ids: All instance IDs associated with the Narrative
        """
        super().__init__(agent_id, user_id, database_client, instance_id, instance_ids)
        self.port = 7803  # MCP Server port

        # Initialize repository (lazy initialization)
        self._job_repo: Optional[JobRepository] = None

        # Build instructions
        agent_id_note = f"""**IMPORTANT**: Your agent_id is `{agent_id}`. When calling job tools, ALWAYS pass `agent_id="{agent_id}"` as the first parameter."""

        self.instructions = """
## Job Module · Background Task Management

### What is a Job?
A Job is an abstraction for background tasks, used to execute work that requires delay, scheduling, or continuous operation. Each Job has explicit trigger conditions and execution logic.

---

### Job Types (Common Knowledge)

| Type | Description | Trigger Condition | Use Case |
|------|-------------|-------------------|----------|
| **ONE_OFF** | One-time task | Execute immediately or at scheduled_time | Single reminder, one-time report |
| **SCHEDULED** | Scheduled task | At scheduled_time | Reminders or reports at specific time points |
| **RECURRING** | Recurring task | Repeats by interval_seconds | Daily reports, periodic checks |
| **ONGOING** | Continuous task | Checks by interval_seconds until end_condition is met | Sales follow-up, goal achievement monitoring |

#### ONGOING Type Details
ONGOING type is designed for continuous tasks (e.g., sales follow-up), features:
- **Continuous execution**: Does not complete after one run, but keeps checking until condition is met
- **End condition**: Describes the task completion criteria via `end_condition`
- **Max iterations**: Optional `max_iterations` to limit maximum execution count
- **CHAT interaction update**: When target user chats with Agent, system automatically analyzes whether end condition is met

---

### Job Status Flow

```
PENDING --after creation--> ACTIVE --after execution--+--> COMPLETED (success)
                              |                       +--> FAILED (failure)
                              |
                              +-- ONGOING type continues until condition is met
```

---

## Current Job Status

{jobs_information}

---

### 🚫 CRITICAL: Job Creation Rules

**1. Jobs I Just Created This Turn**
If there are jobs marked as "Jobs I Just Created" above:
- I already created these jobs based on the user's current request
- I MUST INFORM the user: "I have created a task for you: [task name], it will..."
- DO NOT call job_create again - I already created them!
- Explain what the job will do and when it will execute

**2. Previously Existing Jobs**
If there are jobs marked as "Previously Existing" above:
- These existed before this conversation turn
- Reference them when relevant to user's query
- DO NOT create duplicate jobs

**3. When I MAY Call job_create (RARE CASES ONLY)**
Only use job_create when ALL conditions are met:
- User explicitly requests a NEW scheduled/recurring task
- No matching job exists in any of the lists above
- The task requires delayed/periodic execution (not immediate)

Examples:
- OK: "Remind me to drink water every day at 8 AM" (recurring, not existing)
- OK: "Keep following up with customer Xiaoming until they show purchase intent" (ongoing)
- ❌ User's request matches any job above → Already exists!
- ❌ Multi-step async workflow → I already created job chains for this

**4. If You Must Create a Job**
Required fields:
- `title`: Task title
- `description`: Task description
- `job_type`: "one_off" / "scheduled" / "recurring" / "ongoing"
- `trigger_config`: Trigger configuration
- `payload`: Execution parameters

ONGOING type required fields:
- `trigger_config.interval_seconds`: Check interval (seconds)
- `trigger_config.end_condition`: End condition description (natural language)
- `trigger_config.max_iterations`: Optional, maximum execution count

Optional context parameters:
- **related_entity_id**: str - Target user ID for this job. IMPORTANT rules:
  - **Self-service task** (Agent works, reports back to requester): put requester's user_id
    - Example: User asks "research competitors" → "user_requester_id"
  - **Target-oriented task** (Agent acts on other user): put target user's ID
    - Example: Manager says "sell to xiaoming" → "user_xiaoming"
  - This ID will be used as the main identity when job executes (loading their context, Narrative, etc.)
  - For ONGOING jobs, target user becomes PARTICIPANT of the Narrative

- **narrative_id**: str - Link job to conversation narrative for context loading
  - When job executes, it will load the conversation summary and progress
  - Use when job needs to understand the conversation history/context
  - Typically use the current narrative_id from the ongoing conversation

---

### Job Capabilities
- **Retrieve**: job_retrieval_semantic, job_retrieval_by_id, job_retrieval_by_keywords
- **Create**: job_create (use sparingly, see rules above)
- **Modify**: job_update, job_pause, job_cancel

---

### 🔐 Job Modification Permissions

**CRITICAL: Only the Job CREATOR can modify a Job!**

Each Job has a `user_id` field that records who created it (the creator). Only the creator has permission to command the Agent to perform the following operations:

| Operation | Tool | Permission |
|-----------|------|------------|
| Add guidance | job_update (guidance_text) | Creator only |
| Reschedule | job_update (next_run_time) | Creator only |
| Pause job | job_pause | Creator only |
| Cancel job | job_cancel | Creator only |

**Permission Rules**:
- When a user requests to modify a Job, you MUST verify: **current user == Job's creator (user_id)**
- If mismatch, politely decline: "This task was created by [creator]. Only they can modify it."
- The target user (related_entity_id) has **NO** modification rights - they are only the subject of the task

**Examples**:
- ✅ Sales manager created "Follow up with customer Xiaoming" → Manager can pause/modify/cancel
- ❌ Customer Xiaoming wants to cancel this follow-up task → Decline, Xiaoming is not the creator
- ❌ Another colleague wants to modify task parameters → Decline, they are not the creator

---

### Context Loading
When a job executes with context parameters, JobTrigger automatically loads:

1. **Target User Context** (if related_entity_id provided):
   - Uses related_entity_id as the main user_id for execution
   - Loads that user's Narrative, context, and preferences
   - For ONGOING jobs, target user is added as PARTICIPANT to the Narrative

2. **Narrative Summary** (if narrative_id provided):
   - Current conversation progress summary
   - Historical context from this narrative
   - Helps job understand what has been discussed

3. **Dependency Outputs** (if depends_on_job_ids provided):
   - Results from prerequisite jobs
   - Helps job chain complex workflows

---

### ONGOING Job and CHAT Interaction
When the ONGOING Job's target user (PARTICIPANT) chats with the Agent:
1. System automatically detects ONGOING Jobs associated with the user
2. Analyzes whether conversation content satisfies the Job's `end_condition`
3. Updates Job status based on analysis:
   - Condition met -> Job marked as COMPLETED
   - Not met -> Continue follow-up, record progress

Example: A sales follow-up task's end_condition is "customer shows purchase intent". When the customer says "I'll buy it", the system automatically completes the Job.

"""
        # Replace placeholders
        # self.instructions = self.instructions.replace("{agent_id_note}", agent_id_note)
        self.instance_ids = instance_ids

    def _get_repo(self) -> JobRepository:
        """Get or create JobRepository instance"""
        if self._job_repo is None:
            self._job_repo = JobRepository(self.db)
        return self._job_repo

    # =========================================================================
    # Module Config
    # =========================================================================

    def get_config(self) -> ModuleConfig:
        """
        Return JobModule configuration

        Returns:
            ModuleConfig instance
        """
        return ModuleConfig(
            name="JobModule",
            priority=4,  # Lower priority than Chat, Awareness, SocialNetwork
            enabled=True,
            description="Provides background task creation and management capabilities",
            module_type="task"  # Task module, requires LLM to decide whether to create
        )

    # =========================================================================
    # Hooks
    # =========================================================================

    async def hook_data_gathering(self, ctx_data: ContextData) -> ContextData:
        """
        Collect Job information associated with the current Narrative, populate ctx_data.jobs_information

        Query strategy: prefer narrative_id, fallback to instance_ids
        Display strategy: distinguish between "created this turn" and "previously existing"
        """
        # Collect all Jobs (deduplicated by job_id)
        jobs_map = await self._collect_jobs(ctx_data.narrative_id)

        # Get Job IDs created this turn
        created_this_turn = set(ctx_data.extra_data.get("created_job_ids_this_turn", []))

        # Classify and format
        newly_created = [j for j in jobs_map.values() if j.job_id in created_this_turn]
        existing = [j for j in jobs_map.values() if j.job_id not in created_this_turn]

        ctx_data.jobs_information = await self._format_jobs_information(newly_created, existing)

        if jobs_map:
            logger.info(f"JobModule: created this turn {len(newly_created)}, previously existing {len(existing)}")

        return ctx_data

    async def _collect_jobs(self, narrative_id: Optional[str]) -> Dict[str, JobModel]:
        """Collect Jobs: prefer via narrative_id, fallback via instance_ids"""
        jobs_map: Dict[str, JobModel] = {}

        # Strategy 1: Query via narrative_id
        if narrative_id:
            try:
                jobs = await self._get_repo().get_active_jobs_by_narrative(narrative_id, limit=50)
                for job in jobs:
                    jobs_map[job.job_id] = job
            except Exception as e:
                logger.warning(f"JobModule: narrative_id query failed: {e}")

        # Strategy 2: Query via instance_ids (fallback)
        for instance_id in (self.instance_ids or []):
            try:
                job = await self.get_job_instance_object_by_id(self.agent_id, instance_id)
                if job and job.job_id not in jobs_map:
                    jobs_map[job.job_id] = job
            except Exception:
                pass

        return jobs_map

    async def _format_jobs_information(
        self,
        newly_created: List[JobModel],
        existing: List[JobModel]
    ) -> str:
        """Format Jobs information as Markdown"""
        if not newly_created and not existing:
            return "*No jobs for this conversation.*"

        sections = []

        if newly_created:
            rows = [await self._format_job_row(j) for j in newly_created]
            sections.append(f"""### ✅ Jobs I Just Created ({len(newly_created)})

**I already created these jobs. Inform the user and DO NOT call job_create again.**

| Title | ID | Status | Trigger |
|-------|-----|--------|---------|
""" + "\n".join(rows))

        if existing:
            rows = [await self._format_job_row(j) for j in existing]
            sections.append(f"""### 📋 Existing Jobs ({len(existing)})

| Title | ID | Status | Trigger |
|-------|-----|--------|---------|
""" + "\n".join(rows))

        return "\n\n".join(sections)

    async def _format_job_row(self, job: JobModel) -> str:
        """Format a single Job as a table row"""
        status = job.status.value if hasattr(job.status, 'value') else str(job.status)

        # Trigger info
        trigger = "immediate"
        if job.trigger_config:
            tc = job.trigger_config
            if getattr(tc, 'cron', None):
                trigger = f"cron: {tc.cron}"
            elif getattr(tc, 'run_at', None):
                trigger = f"at: {tc.run_at}"
            elif getattr(tc, 'interval_seconds', None):
                trigger = f"every {tc.interval_seconds}s"

        return f"| {job.title} | `{job.job_id}` | {status} | {trigger} |"

        # === Plan C: Read related_job_ids from extra_data and load Job context ===
        # SocialNetworkModule has already written related_job_ids to extra_data above
        related_job_ids = ctx_data.extra_data.get("related_job_ids", [])
        if related_job_ids:
            logger.info(f"          → JobModule: Found related_job_ids in extra_data: {related_job_ids}")
            related_jobs_context = await self._load_related_jobs_context(related_job_ids, ctx_data)
            if related_jobs_context:
                ctx_data.extra_data["related_jobs_context"] = related_jobs_context
                # Also add to jobs_information so Agent can see it in instructions
                entity_name = ctx_data.extra_data.get("current_entity_name", "this user")
                ctx_data.jobs_information += f"""

### 🎯 Related Sales Tasks for {entity_name}

**Note: This user is a target of the following sales tasks. Keep this context in mind during the conversation.**

{related_jobs_context}
"""
                logger.info(f"          → JobModule: Injected related jobs context for {len(related_job_ids)} jobs")

        return ctx_data

    async def _load_related_jobs_context(self, job_ids: List[str], ctx_data: ContextData) -> Optional[str]:
        """
        Load Job information for related_job_ids and generate context text

        Plan C implementation: Load Job details from Entity's related_job_ids

        Args:
            job_ids: List of Job IDs
            ctx_data: ContextData (for getting entity_name and other info)

        Returns:
            Formatted Job context text, or None
        """
        if not job_ids:
            return None

        try:
            from xyz_agent_context.repository import JobRepository
            from xyz_agent_context.utils.db_factory import get_db_client

            db = await get_db_client()
            job_repo = JobRepository(db)

            jobs_info = []
            for job_id in job_ids:
                job = await job_repo.get_job(job_id)
                if job:
                    status_str = job.status.value if hasattr(job.status, 'value') else str(job.status)
                    job_type_str = job.job_type.value if hasattr(job.job_type, 'value') else str(job.job_type)

                    # Build Job info
                    job_info = f"""**{job.title}** (`{job.job_id}`)
- Type: {job_type_str}
- Status: {status_str}
- Description: {job.description[:200] + '...' if job.description and len(job.description) > 200 else job.description or 'N/A'}
- Payload: {job.payload[:100] + '...' if job.payload and len(job.payload) > 100 else job.payload or 'N/A'}"""

                    jobs_info.append(job_info)

            if jobs_info:
                return "\n\n".join(jobs_info)

        except Exception as e:
            logger.error(f"          ❌ Error loading related jobs context: {e}")

        return None

    async def hook_after_event_execution(self, params: HookAfterExecutionParams) -> Optional[HookCallbackResult]:
        """
        Post-Job execution processing - Use LLM to analyze results and determine completion status

        Trigger conditions (updated 2026-01-21):
        1. working_source == JOB: Job-triggered execution
        2. working_source == CHAT with active JobModule instances: CHAT-triggered but needs to update related Jobs

        Execution flow:
        1. Check trigger conditions (JOB or CHAT with active Jobs)
        2. If JOB-triggered: Use LLM to analyze results and update Job status
        3. If CHAT-triggered: Update related ONGOING Job progress
        4. If one_off job completed/failed, return HookCallbackResult to trigger dependency chain
        5. TODO: Send Inbox notification

        Args:
            params: HookAfterExecutionParams, containing:
                - execution_ctx: Execution context (event_id, agent_id, user_id, working_source)
                - io_data: Input/output (input_content, final_output)
                - trace: Execution trace (event_log, agent_loop_response)
                - ctx_data: Complete context data
                - instance: ModuleInstance (for getting instance_id)

        Returns:
            HookCallbackResult if one_off job completed/failed, otherwise None
        """
        logger.debug(f"          → JobModule.hook_after_event_execution()")

        # 1. Check trigger conditions (2026-01-21 update: also handle Job updates on CHAT trigger)
        # Get currently active JobModule instance IDs
        # Bug fix #1: Use self.instance_ids instead of ctx_data.active_instances (the latter doesn't exist)
        active_job_instance_ids = []
        if self.instance_ids:
            # self.instance_ids contains all instance IDs associated with the current Narrative
            # We need to filter out instances belonging to JobModule (prefixed with "job_")
            active_job_instance_ids = [
                inst_id for inst_id in self.instance_ids
                if inst_id.startswith("job_")
            ]
            logger.debug(f"          → Found {len(active_job_instance_ids)} job instances from self.instance_ids")

        # If not JOB-triggered and no active Job instances, skip
        if params.working_source != WorkingSource.JOB and not active_job_instance_ids:
            logger.debug(f"Not a job and no active job instances, skipping job status update")
            return None

        # 2. Update related ONGOING Jobs on CHAT trigger (added 2026-01-21)
        if params.working_source == WorkingSource.CHAT and active_job_instance_ids:
            logger.info(f"          → CHAT trigger with {len(active_job_instance_ids)} active job instances")
            await self._update_ongoing_jobs_from_chat(
                active_job_instance_ids=active_job_instance_ids,
                chat_content=params.final_output,
                ctx_data=params.ctx_data
            )
            # CHAT trigger doesn't return callback, continue normal flow
            return None

        # 3. Original logic for JOB-triggered execution
        if params.working_source != WorkingSource.JOB:
            return None

        # 2. Get instance (for callback's instance_id)
        instance = params.instance
        if not instance:
            logger.warning(f"            ⚠ No instance available, cannot trigger callback")
            # Continue LLM analysis and database update, but don't return callback
        else:
            logger.info(f"            → Instance: {instance.instance_id}")

        # 3. Collect execution info (via convenience properties)
        final_output = params.final_output
        ctx_data = params.ctx_data
        agent_loop_response = params.agent_loop_response

        # 4. Extract information
        execution_trace = self._extract_execution_trace(agent_loop_response)

        # 5. Get Job's full info (via instance_id)
        current_time = utc_now()
        input_content = params.input_content
        job_info = await self._get_job_info_for_analysis(instance)

        # 6. Build LLM analysis Prompt (different guidance for different job_types)
        prompts = self._build_job_analysis_prompt(
            current_time=current_time,
            input_content=input_content,
            job_info=job_info,
            execution_trace=execution_trace,
            final_output=final_output,
            ctx_data=ctx_data,
        )

        # 6. Call LLM to analyze execution results (retained for detailed info)
        llm_result: JobExecutionResult = await OpenAIAgentsSDK().llm_function(
            instructions=prompts,
            user_input="Please analysis it!",
            output_type=JobExecutionResult,
        )

        # Null check: ensure LLM returned valid results
        if not llm_result or not llm_result.final_output:
            logger.warning("            → LLM returned empty result, skipping job update")
            return None

        result = llm_result.final_output

        logger.info(f"            → LLM analysis result: \n\t{json.dumps(result.model_dump(mode='json'), indent=4)}")

        # 7. Assemble all update data at once, access database only once
        job_id = result.job_id
        if job_id:
            logger.info(f"            → Updating job: {job_id}")
            logger.info(f"            → LLM analysis: status={result.status}, next_run={result.next_run_time}")

            try:
                # 7.1 Get existing job's process list
                existing_job = await self._get_repo().get_job(job_id)
                existing_process = existing_job.process if existing_job and existing_job.process else []

                # 7.2 Extend with new execution records
                updated_process = existing_process + result.process

                # 7.3 Assemble all fields to update (next_run_time intelligently determined by LLM)
                now = utc_now()
                updates = {
                    "status": result.status.value,
                    "process": updated_process,
                    "last_run_time": now,
                    "last_error": result.last_error if result.status == JobStatus.FAILED else None,
                    "updated_at": now,
                    "next_run_time": result.next_run_time,  # LLM intelligent adjustment
                }

                # 7.4 Execute database update in one shot
                await self._update_job_fields(job_id, updates)
                logger.info(f"            ✓ Job {job_id} updated: status={result.status.value}, next_run={result.next_run_time}")

                # 7.5 TODO: Send Inbox notification (if should_notify=True)
                if result.should_notify:
                    logger.info(f"            → Should notify user: {result.notification_summary}")
                    # TODO: Call Inbox module to send notification. For example, notify when errors occur or issues arise.

            except Exception as e:
                logger.error(f"            ❌ Failed to update job {job_id}: {e}")

        # 8. Determine whether to trigger callback based on LLM analysis results
        # Only one_off job's completed/failed triggers callback (activating dependency chain)
        # scheduled job's active status means single execution succeeded, no callback triggered
        is_terminal = result.status in [JobStatus.COMPLETED, JobStatus.FAILED]

        if is_terminal and instance:
            # One-off Job completed or failed, trigger callback to activate dependency chain
            instance_status = (
                InstanceStatus.COMPLETED if result.status == JobStatus.COMPLETED
                else InstanceStatus.FAILED
            )

            callback_result = HookCallbackResult(
                instance_id=instance.instance_id,  # Required: identifies which instance completed
                trigger_callback=True,
                instance_status=instance_status,
                output_data={
                    "job_id": result.job_id,
                    "status": result.status.value,
                    "process": result.process,
                    "notification_summary": result.notification_summary,
                },
                notification_message=result.notification_summary if result.should_notify else None
            )

            logger.info(
                f"            ✅ Job terminal state, returning callback: "
                f"instance_id={instance.instance_id}, status={instance_status.value}"
            )
            return callback_result
        else:
            # Scheduled job continues running, or no instance to trigger callback
            if not instance:
                logger.warning(f"            → Job completed but no instance to trigger callback")
            else:
                logger.info(f"            → Scheduled job, no callback (status={result.status.value})")
            return None

    # =========================================================================
    # CHAT Trigger - ONGOING Job Update (2026-01-21 P0-3)
    # =========================================================================

    async def _update_ongoing_jobs_from_chat(
        self,
        active_job_instance_ids: List[str],
        chat_content: str,
        ctx_data: ContextData
    ) -> None:
        """
        Update related ONGOING Jobs on CHAT trigger

        When a user chats with the Agent, if the current Narrative has active ONGOING Jobs,
        need to check whether the conversation satisfies the Job's end condition (end_condition).

        Flow:
        1. Iterate through active_job_instance_ids
        2. Filter job_type == ONGOING and status == ACTIVE
        3. LLM analyzes this interaction's impact on the Job (whether end_condition is met)
        4. Update status based on OngoingExecutionResult

        Args:
            active_job_instance_ids: Currently active JobModule instance IDs
            chat_content: Agent's final output (conversation content)
            ctx_data: Complete context data
        """
        if not active_job_instance_ids:
            return

        logger.info(f"          → Checking {len(active_job_instance_ids)} active job instances for ONGOING updates")

        for instance_id in active_job_instance_ids:
            try:
                # 1. Get Job object
                job = await self.get_job_instance_object_by_id(self.agent_id, instance_id)
                if not job:
                    logger.debug(f"            → Instance {instance_id}: No job found, skipping")
                    continue

                # 1.5 [Fix 2026-01-22] Only process Jobs where current user is the target user
                # Each ONGOING Job has related_entity_id specifying the target user
                # Only analyze this Job when current conversation user == Job's target user
                current_user_id = ctx_data.user_id if ctx_data else None
                job_target_user = job.related_entity_id

                if job_target_user and current_user_id and job_target_user != current_user_id:
                    logger.info(
                        f"            → Job {job.job_id}: skipping - target user({job_target_user}) != "
                        f"current user({current_user_id})"
                    )
                    continue

                # 2. Filter: only process ONGOING type Jobs with ACTIVE or RUNNING status
                # ONGOING Job status flow:
                #   - ACTIVE: Waiting for user interaction (initial state)
                #   - RUNNING: Job has been triggered, waiting for user response (after JobTrigger trigger)
                # Both statuses need to check end_condition
                if job.job_type != JobType.ONGOING:
                    logger.debug(f"            → Job {job.job_id}: Not ONGOING type ({job.job_type}), skipping")
                    continue

                valid_statuses = {JobStatus.ACTIVE, JobStatus.RUNNING}
                if job.status not in valid_statuses:
                    logger.debug(f"            → Job {job.job_id}: Status {job.status} not in {valid_statuses}, skipping")
                    continue

                # 3. Get end_condition
                end_condition = None
                if job.trigger_config:
                    end_condition = job.trigger_config.end_condition

                if not end_condition:
                    logger.debug(f"            → Job {job.job_id}: No end_condition defined, skipping LLM analysis")
                    continue

                logger.info(f"            → Analyzing ONGOING Job {job.job_id} against chat interaction")

                # 4. Build LLM analysis Prompt
                current_time = utc_now()
                user_query = ctx_data.input_content if ctx_data else ""

                prompt = f"""
Analyze if the current chat interaction satisfies the end condition of an ONGOING job.

## Job Information

**Job ID**: {job.job_id}
**Title**: {job.title}
**Description**: {job.description}
**Payload**: {job.payload[:500] if job.payload else 'N/A'}...

**End Condition**: {end_condition}

**Current Iteration**: {job.iteration_count}
**Max Iterations**: {job.trigger_config.max_iterations if job.trigger_config and job.trigger_config.max_iterations else 'No limit'}

## Current Chat Interaction

**User Query**: {user_query}

**Agent Response**: {chat_content[:1000] if chat_content else 'N/A'}...

## Your Task

Determine if this chat interaction indicates that the job's end condition has been met.

For example, if the end condition is "customer shows purchase intent or explicit rejection":
- Customer says "I'll buy it" -> end condition MET
- Customer says "No thanks, I don't need it" -> end condition MET
- Customer asks "What's the price?" -> end condition NOT MET (still interested, continuing conversation)

## Return Fields

1. **job_id**: "{job.job_id}"

2. **is_end_condition_met**: true/false - Does this interaction satisfy the end condition?

3. **end_condition_reason**: Detailed explanation of why the condition is/isn't met

4. **should_continue**: true/false - Should the job continue?
   - false if end_condition is met
   - false if max_iterations reached (current: {job.iteration_count}, max: {job.trigger_config.max_iterations if job.trigger_config and job.trigger_config.max_iterations else 'unlimited'})
   - true otherwise

5. **progress_summary**: 1-2 sentence summary of what happened in this interaction

6. **process**: 2-3 concise descriptions of actions taken
"""

                # 5. Call LLM analysis
                llm_result = await OpenAIAgentsSDK().llm_function(
                    instructions=prompt,
                    user_input="Please analyze this interaction.",
                    output_type=OngoingExecutionResult,
                )

                if not llm_result or not llm_result.final_output:
                    logger.warning(f"            → LLM returned empty result for job {job.job_id}")
                    continue

                result = llm_result.final_output
                logger.info(f"            → LLM analysis: is_end_condition_met={result.is_end_condition_met}, should_continue={result.should_continue}")

                # 6. Update Job status
                updates = {
                    "updated_at": current_time,
                }

                # Accumulate process records
                existing_process = job.process if job.process else []
                if result.process:
                    updates["process"] = existing_process + result.process

                # If end condition is met or should_continue=False
                if result.is_end_condition_met or not result.should_continue:
                    updates["status"] = JobStatus.COMPLETED.value
                    logger.info(f"            ✓ Job {job.job_id} completed: {result.end_condition_reason}")
                else:
                    # Continue execution, increment iteration_count
                    updates["iteration_count"] = job.iteration_count + 1

                    # Check if max_iterations reached
                    max_iter = job.trigger_config.max_iterations if job.trigger_config else None
                    if max_iter and updates["iteration_count"] >= max_iter:
                        updates["status"] = JobStatus.COMPLETED.value
                        logger.info(f"            ✓ Job {job.job_id} completed: max iterations reached")

                # Execute update
                await self._update_job_fields(job.job_id, updates)
                logger.info(f"            ✓ Job {job.job_id} updated: {list(updates.keys())}")

            except Exception as e:
                logger.error(f"            ❌ Error processing job instance {instance_id}: {e}")
                continue

    # =========================================================================
    # Database Operations (Internal - for hook_after_event_execution)
    # =========================================================================

    async def _update_job_fields(self, job_id: str, updates: Dict[str, Any]) -> int:
        """
        Update multiple fields of a Job at once

        Uses JobRepository.update_job() for data access, following the Repository pattern.

        Args:
            job_id: Job ID
            updates: Dictionary of fields to update, supports:
                - status: str | JobStatus
                - process: List[str]
                - last_run_time: datetime
                - next_run_time: datetime | None
                - last_error: str | None

        Returns:
            Number of affected rows
        """
        if not updates:
            return 0

        # Use JobRepository to update
        return await self._get_repo().update_job(job_id, updates)

    def _extract_execution_trace(self, agent_loop_response: List[Any]) -> str:
        """Delegate to _job_analysis module"""
        return extract_execution_trace(agent_loop_response)

    def _extract_context_info(self, ctx_data: Any) -> str:
        """Delegate to _job_analysis module"""
        return extract_context_info(ctx_data)

    async def _get_job_info_for_analysis(self, instance: Optional[Any]) -> Dict[str, Any]:
        """
        Get complete Job information for LLM analysis

        Retrieves the Job object via instance_id and extracts key information:
        - job_id, job_type, title, description
        - trigger_config (contains end_condition, interval_seconds, max_iterations, etc.)
        - iteration_count, process (historical execution records)

        Args:
            instance: ModuleInstance instance

        Returns:
            Dictionary containing complete Job information
        """
        if not instance or not instance.instance_id:
            return {}

        try:
            job = await self.get_job_instance_object_by_id(self.agent_id, instance.instance_id)
            if not job:
                return {}

            # Extract trigger_config information
            trigger_info = {}
            if job.trigger_config:
                trigger_info = {
                    "end_condition": job.trigger_config.end_condition,
                    "interval_seconds": job.trigger_config.interval_seconds,
                    "max_iterations": job.trigger_config.max_iterations,
                    "cron": job.trigger_config.cron,
                }

            return {
                "job_id": job.job_id,
                "job_type": job.job_type.value if job.job_type else None,
                "title": job.title,
                "description": job.description,
                "payload": job.payload[:500] if job.payload else None,
                "trigger_config": trigger_info,
                "iteration_count": job.iteration_count or 0,
                "process": job.process or [],  # Historical execution records
                "status": job.status.value if job.status else None,
            }
        except Exception as e:
            logger.error(f"Failed to get job info for analysis: {e}")
            return {}

    def _build_job_analysis_prompt(
        self,
        current_time: Any,
        input_content: str,
        job_info: Dict[str, Any],
        execution_trace: str,
        final_output: str,
        ctx_data: Any,
    ) -> str:
        """Delegate to _job_analysis module"""
        return build_job_analysis_prompt(
            current_time, input_content, job_info,
            execution_trace, final_output, ctx_data,
        )

    # =========================================================================
    # MCP Server
    # =========================================================================

    async def get_mcp_config(self) -> Optional[MCPServerConfig]:
        """
        Return MCP Server configuration

        JobModule provides an MCP Server containing:
        - job_create: Create Job
        - job_retrieval: Query Job

        Returns:
            MCPServerConfig instance
        """
        return MCPServerConfig(
            server_name="job_module",
            server_url=f"http://127.0.0.1:{self.port}/sse",
            type="sse"
        )

    def create_mcp_server(self) -> Optional[Any]:
        """
        Create MCP Server instance

        Tool definitions have been extracted to _job_mcp_tools.py.
        """
        return create_job_mcp_server(self.port, JobModule.get_mcp_db_client)


    # =========================================================================
    # Database Operations (Internal)
    # =========================================================================

    async def _create_job(
        self,
        title: str,
        description: str,
        job_type: str,
        trigger_config: dict,
        payload: str,
        notification_method: str = "inbox"
    ) -> Dict[str, Any]:
        """
        Create a new Job (internal method).

        This method is called by the job_create MCP tool. It validates parameters,
        creates the job record with embedding, and returns the result.

        Args:
            title: Job title
            description: Job description
            job_type: Job type ("one_off" or "scheduled")
            trigger_config: Trigger configuration dict
            payload: Execution instruction
            notification_method: Notification method

        Returns:
            Dict with success status, job_id, and message
        """
        try:
            # Validate job_type
            try:
                job_type_enum = JobType(job_type.lower())
            except ValueError:
                return {
                    "success": False,
                    "error": f"Invalid job_type: {job_type}. Must be 'one_off' or 'scheduled'"
                }

            # Parse trigger_config
            trigger = TriggerConfig(**trigger_config)

            # Validate trigger config based on job type
            if job_type_enum == JobType.ONE_OFF and not trigger.run_at:
                return {
                    "success": False,
                    "error": "one_off job requires 'run_at' in trigger_config"
                }

            if job_type_enum == JobType.SCHEDULED:
                if not trigger.cron and not trigger.interval_seconds:
                    return {
                        "success": False,
                        "error": "scheduled job requires 'cron' or 'interval_seconds' in trigger_config"
                    }

            # Generate job_id
            job_id = f"job_{uuid4().hex[:12]}"

            # Calculate next run time (using unified calculation function)
            logger.debug(f"trigger_config: {trigger_config}")
            logger.debug(f"trigger.run_at: {trigger.run_at}, type: {type(trigger.run_at)}")
            logger.debug(f"trigger.cron: {trigger.cron}")
            logger.debug(f"trigger.interval_seconds: {trigger.interval_seconds}")

            next_run_time = calculate_next_run_time(job_type_enum, trigger)
            logger.debug(f"Calculated next_run_time: {next_run_time}")

            # Generate embedding (for semantic search)
            embedding_text = prepare_job_text_for_embedding(title, description, payload)
            embedding = await get_embedding(embedding_text)

            # Create JobModule Instance (each Job has its own instance, task-level isolation)
            instance_factory = InstanceFactory(self.db)
            job_info = {
                "job_id": job_id,
                "title": title,
                "description": description,
                "job_type": job_type,
                "payload": payload,
            }
            job_instance = await instance_factory.create_job_instance(
                agent_id=self.agent_id,
                user_id=self.user_id or "unknown",
                job_info=job_info
            )
            instance_id = job_instance.instance_id
            logger.debug(f"Created job instance: {instance_id}")

            # Use repository to create job
            await self._get_repo().create_job(
                agent_id=self.agent_id,
                user_id=self.user_id or "unknown",
                job_id=job_id,
                title=title,
                description=description,
                job_type=job_type_enum,
                trigger_config=trigger,
                payload=payload,
                instance_id=instance_id,
                notification_method=notification_method,
                next_run_time=next_run_time,
                embedding=embedding
            )

            logger.info(f"Created job: {job_id} with instance: {instance_id}")

            return {
                "success": True,
                "job_id": job_id,
                "message": f"Job '{title}' created successfully. It will be executed according to the schedule."
            }

        except Exception as e:
            logger.error(f"Error creating job: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def _get_user_active_jobs(self, user_id: str) -> List[JobModel]:
        """
        Get active jobs for a user.

        Returns jobs with status PENDING or ACTIVE.

        Args:
            user_id: User ID

        Returns:
            List of active JobModel instances
        """
        try:
            repo = self._get_repo()

            # Get pending jobs
            pending_jobs = await repo.get_jobs_by_user(
                user_id=user_id,
                status=JobStatus.PENDING,
                limit=50
            )

            # Get active jobs
            active_jobs = await repo.get_jobs_by_user(
                user_id=user_id,
                status=JobStatus.ACTIVE,
                limit=50
            )

            return pending_jobs + active_jobs

        except Exception as e:
            logger.error(f"Error getting active jobs: {e}")
            return []

    async def _update_job_status(
        self,
        job_id: str,
        status: JobStatus,
        error_message: Optional[str] = None
    ) -> bool:
        """
        Update job status.

        Args:
            job_id: Job ID
            status: New status
            error_message: Error message (if failed)

        Returns:
            True if successful
        """
        try:
            affected = await self._get_repo().update_job_status(
                job_id=job_id,
                status=status,
                error_message=error_message
            )
            return affected > 0

        except Exception as e:
            logger.error(f"Error updating job status: {e}")
            return False

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _format_jobs_for_display(self, jobs: List[JobModel]) -> str:
        """
        Format job list for display in agent instructions.

        Shows a brief summary of active jobs so the Agent knows what
        tasks are running. Only shows first 10 jobs with essential info.

        Args:
            jobs: List of JobModel instances

        Returns:
            Markdown formatted string
        """
        if not jobs:
            return "*No active jobs.*"

        # Convert to summary format
        summaries = []
        for job in jobs[:10]:
            next_run = job.next_run_time.strftime("%Y-%m-%d %H:%M") if job.next_run_time else "N/A"
            summaries.append({
                "job_id": job.job_id,
                "title": job.title,
                "next_run_time": next_run,
                "job_type": job.job_type.value,
                "status": job.status.value,
            })

        return format_jobs_for_display(summaries, max_display=10)
    
    # ========================================================================= 
    # Instance Parts
    # =========================================================================

    async def get_instance_object_candidates(self, agent_id: str, user_id: str, user_query: str) -> List[Any]:
        """
        Return a list of Job candidates semantically related to the user query.

        Calls the job_retrieval_semantic tool via FastMCP Client for semantic search.
        Uses in-memory invocation, no network calls.

        Args:
            agent_id: Agent ID
            user_id: User ID
            user_query: User query text

        Returns:
            List of matching Jobs
        """
        mcp_server = self.create_mcp_server()

        if mcp_server is None:
            return []

        try:
            # Use Client wrapping Server for in-memory invocation
            client = Client(mcp_server)
            async with client:
                result = await client.call_tool("job_retrieval_semantic", {
                    "agent_id": agent_id,
                    "query": user_query,
                    "user_id": user_id
                })

            # Parse return result
            # call_tool returns CallToolResult object, data in content[0].text is a JSON string
            if hasattr(result, 'content') and result.content:
                text_content = result.content[0].text
                data = json.loads(text_content)
                if data.get("success"):
                    return data.get("jobs", [])

            return []

        except Exception as e:
            logger.error(f"Error in get_instance_object_candidates: {e}")
            return []
        
    async def get_job_instance_object_by_id(self, agent_id: str, instance_id: str) -> Any:
        """
        Get the associated Job object by instance_id

        Note: instance_id and job_id are different!
        - instance_id: ModuleInstance's ID
        - job_id: Job record's ID
        Job is linked to ModuleInstance via the instance_id field.

        Args:
            agent_id: Agent ID
            instance_id: Instance ID (not job_id!)

        Returns:
            Job object, or None if not found
        """
        try:
            # Use JobRepository to query by instance_id
            jobs = await self._get_repo().get_jobs_by_instance(instance_id, limit=1)
            if jobs:
                return jobs[0]
            return None

        except Exception as e:
            logger.error(f"Error in get_job_instance_object_by_id: {e}")
            return None
            
    
    def create_instance_object(self, **kwargs) -> Any:
        raise NotImplementedError("create_instance_object is not implemented")
    
    def update_instance_object(self, **kwargs) -> None:
        raise NotImplementedError("update_instance_object is not implemented")
    
    def delete_instance_object(self, **kwargs) -> None:
        raise NotImplementedError("delete_instance_object is not implemented")
    
