"""
@file_name: services.py
@author: Bin Liang
@date: 2026-09-04
@description: Services builtin.job exposes on the process locator (``jobs.instances``, ``jobs.run_once``).

Onboarding / Arena provisioning create jobs and the Manyfold sync route runs
one on demand; they resolve these through ``narranexus.contracts.services`` so
the platform imports nothing from the job module.
"""
from __future__ import annotations

from typing import Any

from narranexus.contracts.services import JOB_INSTANCES, JOB_RUN_ONCE


def job_instances(db: Any):
    from narranexus_plugins.job_module.job_service import JobInstanceService

    return JobInstanceService(db)


async def run_once(agent_id: str, job_id: str):
    from narranexus_plugins.job_module.run_once import run_job_once

    return await run_job_once(agent_id, job_id)


SERVICES = ((JOB_INSTANCES, job_instances), (JOB_RUN_ONCE, run_once))

__all__ = ["SERVICES", "job_instances", "run_once"]
