"""
@file_name: service_refs.py
@author: Bin Liang
@date: 2026-09-04
@description: Typed service refs builtins expose for the platform (skills workspace, job instances, run-once).

The platform used to import a builtin module whenever it needed one of its
services (``SkillModule`` in the bundle importer / marketplace / skill sync,
``JobInstanceService`` in onboarding and Arena provisioning, the JobTrigger
execution body in the Manyfold sync route). Each of those is now a service
the owning builtin exposes on ``Registries.services`` under one of these
refs; the platform ``require``s it (fail loud when the builtin is disabled)
or ``try_require``s it where a degraded path exists.
"""
from __future__ import annotations

from typing import Any

from narranexus.kernel.plugins.services import ServiceRef

# ``(agent_id, user_id) -> SkillWorkspace`` (contracts.skill.SkillWorkspace) — builtin.skills
SKILL_WORKSPACES = ServiceRef[Any]("skills.workspaces")
# ``(db) -> object with create_job_with_instance(...)`` — builtin.job
JOB_INSTANCES = ServiceRef[Any]("jobs.instances")
# ``async (agent_id, job_id) -> contracts.job.JobRunOutcome`` — builtin.job
JOB_RUN_ONCE = ServiceRef[Any]("jobs.run_once")

__all__ = ["JOB_INSTANCES", "JOB_RUN_ONCE", "SKILL_WORKSPACES"]
