"""
@file_name: plugin_services.py
@author: Bin Liang
@date: 2026-09-04
@description: Platform-side accessors for services builtins expose (skills workspace, job instances, run-once).

Thin wrappers over ``KERNEL_REGISTRIES.services`` + ``kernel/plugins/service_refs``
so the bundle importer, marketplace, skill sync, onboarding and Arena
provisioning never name a builtin module. ``require`` fails loud (UnknownEntry)
when the owning builtin is disabled; callers that have a degraded path use
the ``try_*`` variants.
"""
from __future__ import annotations

from typing import Any, Optional

from narranexus.contracts.skill import SkillWorkspace
from narranexus.kernel.plugins.service_refs import JOB_INSTANCES, JOB_RUN_ONCE, SKILL_WORKSPACES


def _services():
    import narranexus.platform.module_system  # noqa: F401 — registers the builtins' services (idempotent)
    from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES

    return KERNEL_REGISTRIES.services


def skill_workspace(agent_id: str, user_id: Optional[str]) -> SkillWorkspace:
    """An agent's skills workspace (builtin.skills); raises UnknownEntry when skills are disabled."""
    return _services().require(SKILL_WORKSPACES)(agent_id, user_id)


def job_instances(db: Any) -> Any:
    """The job instance service over ``db`` (builtin.job); raises UnknownEntry when jobs are disabled."""
    return _services().require(JOB_INSTANCES)(db)


def try_job_run_once() -> Any:
    """``async (agent_id, job_id) -> JobRunOutcome`` or None when builtin.job is disabled."""
    return _services().try_require(JOB_RUN_ONCE)


__all__ = ["job_instances", "skill_workspace", "try_job_run_once"]
