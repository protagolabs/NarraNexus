"""
@file_name: test_module_poller_no_narrative_dependency.py
@author: Bin Liang
@date: 2026-09-09
@description: B-16 — ModulePoller._process_completed_instance must resolve
dependents via the narrative-independent path when the completed instance
has no narrative_id (e.g. a Job created via /api/jobs/complex).
"""
from __future__ import annotations

import pytest

from narranexus.platform.repository import InstanceRepository
from narranexus.platform.schema.instance_schema import InstanceStatus, ModuleInstanceRecord
from narranexus.platform.services.module_poller import CompletedInstanceInfo, ModulePoller

AGENT_ID = "agent_1"


async def _seed_instance(db, instance_id, *, status, dependencies=None):
    repo = InstanceRepository(db)
    await repo.create_instance(ModuleInstanceRecord(
        instance_id=instance_id,
        module_class="JobModule",
        agent_id=AGENT_ID,
        status=status,
        dependencies=dependencies or [],
    ))


def _make_poller(db_client) -> ModulePoller:
    poller = ModulePoller.__new__(ModulePoller)
    poller._db = db_client
    poller._instance_repo = None
    poller._link_repo = None
    return poller


@pytest.mark.asyncio
async def test_no_narrative_id_activates_dependent_via_no_narrative_path(db_client):
    await _seed_instance(db_client, "job_a", status=InstanceStatus.ACTIVE)
    await _seed_instance(db_client, "job_b", status=InstanceStatus.BLOCKED, dependencies=["job_a"])
    # job_a is "completed" per module_instances (mirrors what JobTrigger's
    # raw-SQL _update_instance_completed already did before the poller runs).
    await InstanceRepository(db_client).update_status(
        "job_a", InstanceStatus.COMPLETED, completed_at=None,
    )

    poller = _make_poller(db_client)
    info = CompletedInstanceInfo(
        instance_id="job_a", narrative_id="", agent_id=AGENT_ID,
        user_id=None, module_class="JobModule",
    )

    await poller._process_completed_instance(info)

    row = await InstanceRepository(db_client).get_by_instance_id("job_b")
    assert row.status == InstanceStatus.ACTIVE.value


@pytest.mark.asyncio
async def test_narrative_id_present_still_uses_the_narrative_scoped_path(db_client, monkeypatch):
    """Regression guard: instances that DO have a narrative must keep using
    the existing handle_completion path, not silently switch over."""
    called = {}

    async def _fake_handle_completion(self, narrative_id, instance_id, new_status):
        called["narrative_id"] = narrative_id
        return []

    async def _fake_no_narrative(self, instance_id, new_status):
        called["no_narrative_called"] = True
        return []

    monkeypatch.setattr(
        "narranexus.platform.narrative.InstanceHandler.handle_completion",
        _fake_handle_completion,
    )
    monkeypatch.setattr(
        "narranexus.platform.narrative.InstanceHandler.handle_completion_no_narrative",
        _fake_no_narrative,
    )

    await _seed_instance(db_client, "job_a", status=InstanceStatus.ACTIVE)
    await InstanceRepository(db_client).update_status(
        "job_a", InstanceStatus.COMPLETED, completed_at=None,
    )

    poller = _make_poller(db_client)
    info = CompletedInstanceInfo(
        instance_id="job_a", narrative_id="nar_1", agent_id=AGENT_ID,
        user_id=None, module_class="JobModule",
    )

    await poller._process_completed_instance(info)

    assert called.get("narrative_id") == "nar_1"
    assert "no_narrative_called" not in called
