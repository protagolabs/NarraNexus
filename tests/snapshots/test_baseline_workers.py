"""
@file_name: test_baseline_workers.py
@author: Bin Liang
@date: 2026-09-03
@description: Pin the worker supervisor's canonical worker set and startup order.
"""
from __future__ import annotations

from tests.snapshots._approval import approve


def test_worker_specs_are_unchanged():
    from xyz_agent_context.module.run_worker_supervisor import ALL_WORKERS, WORKER_SPECS

    approve("workers", {"order": list(ALL_WORKERS), "specs": sorted(WORKER_SPECS)})
