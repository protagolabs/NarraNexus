"""
@file_name: __init__.py
@author: NetMind.AI
@date: 2025-12-25
@description: JobModule module

Contains:
- JobModule - Job background task module
- JobInstanceService - Job unified creation service
"""

from .job_module import JobModule
from .job_service import JobInstanceService

# Public read helpers for the AgentDataStore seam + backend job routes (so
# callers import the PACKAGE, not the private _job_reads leaf).
from ._job_reads import (
    fetch_job_by_id,
    search_jobs_semantic,
    search_jobs_by_keywords,
)
from ._job_writes import (
    update_job_from_args,
    create_job_from_args,
    pause_job_from_args,
    cancel_job_from_args,
)

# Register the Job channel handler so chat_module recognises
# job-triggered replies and renders job-source rows with a distinct
# prefix. Jobs reuse send_message_to_user_directly when the agent
# decides to message the user about a job outcome; the prefix is what
# tells the LLM "this stored row was emitted by a scheduled task, not
# a live UI conversation" so it can weigh follow-ups appropriately.
from xyz_agent_context.channel.message_source_handler import (
    MessageSourceHandler,
    MessageSourceRegistry,
)

try:
    MessageSourceRegistry.register(MessageSourceHandler(
        name="job",
        user_reply_tool_names=("send_message_to_user_directly",),
        row_prefix_template="[Background Job]",
    ))
except ValueError:
    pass


__all__ = [
    "JobModule",
    "JobInstanceService",
    "fetch_job_by_id",
    "search_jobs_semantic",
    "search_jobs_by_keywords",
    "update_job_from_args",
    "create_job_from_args",
    "pause_job_from_args",
    "cancel_job_from_args",
]
