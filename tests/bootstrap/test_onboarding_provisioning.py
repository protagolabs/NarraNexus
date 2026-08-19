"""
@file_name: test_onboarding_provisioning.py
@author: Bin Liang
@date: 2026-08-19
@description: Unit tests for ensure_guide_agent — the login-time guide-agent
provisioning seam. Pins: the env kill-switch, user-level idempotency (marker
survives agent deletion), the has-agents skip (existing users keep their
peace), the happy-path sequence (provision → tag → bootstrap-with-extras →
guide skill → ACTIVE ongoing check-in job → marker), and best-effort
semantics for the job step.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import xyz_agent_context.bootstrap.onboarding.provisioning as ob
from xyz_agent_context.bootstrap.onboarding.personas import PERSONAS


class Recorder:
    def __init__(self):
        self.calls = []


class FakeUserRepo:
    """In-memory users table: user_id -> metadata dict (None = no user)."""

    instances = None  # set per-test to share state across constructions

    def __init__(self, db):
        self.state = FakeUserRepo.instances

    async def get_user(self, user_id):
        if user_id not in self.state:
            return None
        return SimpleNamespace(
            user_id=user_id, metadata=self.state[user_id], timezone="Asia/Shanghai"
        )

    async def update_user(self, user_id, updates):
        self.state[user_id] = updates.get("metadata", self.state.get(user_id))
        return 1


def _wire(monkeypatch, rec, *, users, agents=(), job_fails=False):
    FakeUserRepo.instances = users

    class FakeAgentRepo:
        def __init__(self, db):
            pass

        async def find(self, filters=None):
            rec.calls.append(("find", filters))
            return list(agents)

        async def get_agent(self, agent_id):
            return SimpleNamespace(agent_id=agent_id, agent_metadata={})

        async def update_agent(self, agent_id, updates):
            rec.calls.append(("update_agent", updates))

    async def fake_provision(db, **kw):
        rec.calls.append(("provision", kw))
        return SimpleNamespace(agent_id=kw["agent_id"], bootstrap_active=True, warnings=[])

    async def fake_apply_bootstrap(db, **kw):
        rec.calls.append(("apply_bootstrap", kw))

    class FakeSkillSvc:
        async def install(self, agent_id, user_id, skill_id, version=None):
            rec.calls.append(("install_skill", skill_id))
            return SimpleNamespace(status="installed")

    class FakeJobSvc:
        def __init__(self, db):
            pass

        async def create_job_with_instance(self, **kw):
            rec.calls.append(("create_job", kw))
            if job_fails:
                return {"success": False, "error": "boom"}
            return {"success": True, "job_id": "job_123"}

    monkeypatch.setattr(
        "xyz_agent_context.repository.user_repository.UserRepository", FakeUserRepo
    )
    monkeypatch.setattr("xyz_agent_context.repository.AgentRepository", FakeAgentRepo)
    monkeypatch.setattr(
        "xyz_agent_context.bootstrap.provision.provision_new_agent", fake_provision
    )
    monkeypatch.setattr(
        "xyz_agent_context.bootstrap.profiles.apply_bootstrap", fake_apply_bootstrap
    )
    monkeypatch.setattr(
        "xyz_agent_context.marketplace.skill_marketplace_service.SkillMarketplaceService",
        FakeSkillSvc,
    )
    monkeypatch.setattr(
        "xyz_agent_context.module.job_module.job_service.JobInstanceService", FakeJobSvc
    )
    monkeypatch.setattr(ob, "is_cloud_mode", lambda: True)


def test_kill_switch_disables_everything(monkeypatch):
    rec = Recorder()
    _wire(monkeypatch, rec, users={"u1": {}})
    monkeypatch.setenv(ob.ENV_FLAG, "0")
    res = asyncio.run(ob.ensure_guide_agent(object(), "u1"))
    assert res == {"skipped": "disabled"}
    assert rec.calls == []  # zero repo/db work when disabled


def test_flag_default_is_on(monkeypatch):
    monkeypatch.delenv(ob.ENV_FLAG, raising=False)
    assert ob.is_guide_agent_enabled() is True
    monkeypatch.setenv(ob.ENV_FLAG, "false")
    assert ob.is_guide_agent_enabled() is False


def test_marker_makes_it_idempotent(monkeypatch):
    rec = Recorder()
    _wire(
        monkeypatch,
        rec,
        users={"u1": {ob.ONBOARDING_METADATA_KEY: {ob.GUIDE_METADATA_FLAG: True}}},
    )
    monkeypatch.delenv(ob.ENV_FLAG, raising=False)
    res = asyncio.run(ob.ensure_guide_agent(object(), "u1"))
    assert res == {"skipped": "already_provisioned"}
    assert all(c[0] != "provision" for c in rec.calls)


def test_missing_user_skips(monkeypatch):
    rec = Recorder()
    _wire(monkeypatch, rec, users={})
    monkeypatch.delenv(ob.ENV_FLAG, raising=False)
    res = asyncio.run(ob.ensure_guide_agent(object(), "ghost"))
    assert res == {"skipped": "no_user"}


def test_user_with_agents_gets_marker_but_no_guide(monkeypatch):
    rec = Recorder()
    users = {"u1": {}}
    _wire(monkeypatch, rec, users=users, agents=[SimpleNamespace(agent_id="a1")])
    monkeypatch.delenv(ob.ENV_FLAG, raising=False)
    res = asyncio.run(ob.ensure_guide_agent(object(), "u1"))
    assert res == {"skipped": "has_agents"}
    assert all(c[0] != "provision" for c in rec.calls)
    # Marker written so later logins short-circuit before the agents query.
    assert users["u1"][ob.ONBOARDING_METADATA_KEY][ob.GUIDE_METADATA_FLAG] is True


def test_happy_path_full_sequence(monkeypatch):
    rec = Recorder()
    users = {"u1": {}}
    _wire(monkeypatch, rec, users=users)
    monkeypatch.delenv(ob.ENV_FLAG, raising=False)
    res = asyncio.run(ob.ensure_guide_agent(object(), "u1"))

    assert res["provisioned"] is True and res["warnings"] == []
    steps = [c[0] for c in rec.calls]
    assert steps.index("provision") < steps.index("apply_bootstrap")
    assert "install_skill" in steps and "create_job" in steps

    prov_kw = next(kw for name, kw in rec.calls if name == "provision")
    # The shared seam must NOT render a fallback bootstrap — the extras-laden
    # apply_bootstrap below is the real one.
    assert prov_kw["bootstrap_profile"] == "none"
    assert any(p["awareness"] in prov_kw["awareness"] for p in PERSONAS)
    assert prov_kw["agent_name"].count("_") >= 2  # three-group random name

    bs_kw = next(kw for name, kw in rec.calls if name == "apply_bootstrap")
    assert bs_kw["profile"].name == "onboarding"
    extra = bs_kw["ctx"].extra
    assert {"persona_key", "topic_index", "is_local"} <= set(extra)
    assert extra["is_local"] is False  # is_cloud_mode() wired True

    assert ("install_skill", ob.GUIDE_SKILL_ID) in rec.calls

    job_kw = next(kw for name, kw in rec.calls if name == "create_job")
    assert job_kw["job_type"] == "ongoing"
    tc = job_kw["trigger_config"]
    assert tc["interval_seconds"] == 86400
    assert tc["max_iterations"] == 14
    assert tc["end_condition"]
    assert tc["timezone"] == "Asia/Shanghai"
    assert job_kw["title"] == ob.CHECKIN_JOB_TITLE

    # Agent tagged for ops/secondary idempotency.
    tag = next(kw for name, kw in rec.calls if name == "update_agent")
    assert tag["agent_metadata"]["provisioned_source"] == "onboarding"

    assert users["u1"][ob.ONBOARDING_METADATA_KEY][ob.GUIDE_METADATA_FLAG] is True


def test_local_mode_flows_into_ctx(monkeypatch):
    rec = Recorder()
    _wire(monkeypatch, rec, users={"u1": {}})
    monkeypatch.setattr(ob, "is_cloud_mode", lambda: False)
    monkeypatch.delenv(ob.ENV_FLAG, raising=False)
    asyncio.run(ob.ensure_guide_agent(object(), "u1"))
    bs_kw = next(kw for name, kw in rec.calls if name == "apply_bootstrap")
    assert bs_kw["ctx"].extra["is_local"] is True
    prov_kw = next(kw for name, kw in rec.calls if name == "provision")
    assert "LOCAL INSTALL" in prov_kw["awareness"]


def test_job_failure_is_best_effort_and_marker_still_written(monkeypatch):
    rec = Recorder()
    users = {"u1": {}}
    _wire(monkeypatch, rec, users=users, job_fails=True)
    monkeypatch.delenv(ob.ENV_FLAG, raising=False)
    res = asyncio.run(ob.ensure_guide_agent(object(), "u1"))
    assert res["provisioned"] is True
    assert any("checkin_job" in w for w in res["warnings"])
    assert res["job_id"] is None
    assert users["u1"][ob.ONBOARDING_METADATA_KEY][ob.GUIDE_METADATA_FLAG] is True


def test_marker_write_preserves_sibling_metadata(monkeypatch):
    rec = Recorder()
    users = {"u1": {"other_key": {"keep": 1},
                    ob.ONBOARDING_METADATA_KEY: {"first_agent_created": True}}}
    _wire(monkeypatch, rec, users=users)
    monkeypatch.delenv(ob.ENV_FLAG, raising=False)
    asyncio.run(ob.ensure_guide_agent(object(), "u1"))
    meta = users["u1"]
    assert meta["other_key"] == {"keep": 1}
    assert meta[ob.ONBOARDING_METADATA_KEY]["first_agent_created"] is True
    assert meta[ob.ONBOARDING_METADATA_KEY][ob.GUIDE_METADATA_FLAG] is True
