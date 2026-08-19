"""
@file_name: test_onboarding_provisioning.py
@author: Bin Liang
@date: 2026-08-19
@description: Unit tests for ensure_guide_agent — the login-time guide-agent
provisioning seam. Pins: the env kill-switch + the backfill brake, user-level
idempotency on a TOP-LEVEL metadata key (survives agent deletion AND the
checklist endpoint's onboarding_progress rewrite), the has-agents skip, the
claim-FIRST ordering (marker before provisioning — the concurrency defense),
the happy-path sequence (single provision call carrying profile+extras →
tag → guide skill → daily SCHEDULED job), best-effort semantics for the job
step, and per-user in-process serialization of concurrent calls.

Collaborators are faked here (parameter-pipe assertions); the persisted-row
contract is covered by test_onboarding_provisioning_integration.py, which
runs the real thing against the db_client fixture.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import backend.onboarding.provisioning as ob
from backend.onboarding.personas import PERSONAS


class Recorder:
    def __init__(self):
        self.calls = []


class FakeUserRepo:
    """In-memory users table: user_id -> metadata dict (None = no user)."""

    instances = None  # set per-test to share state across constructions
    recorder = None

    def __init__(self, db):
        self.state = FakeUserRepo.instances

    async def get_user(self, user_id):
        if user_id not in self.state:
            return None
        return SimpleNamespace(
            user_id=user_id, metadata=self.state[user_id], timezone="Asia/Shanghai"
        )

    async def update_user(self, user_id, updates):
        if FakeUserRepo.recorder is not None:
            FakeUserRepo.recorder.calls.append(("update_user", updates))
        self.state[user_id] = updates.get("metadata", self.state.get(user_id))
        return 1


def _wire(monkeypatch, rec, *, users, agents=(), job_fails=False, provision_delay=0.0):
    FakeUserRepo.instances = users
    FakeUserRepo.recorder = rec

    class FakeAgentRepo:
        def __init__(self, db):
            pass

        async def find_one(self, filters=None):
            rec.calls.append(("find_one", filters))
            return list(agents)[0] if agents else None

        async def get_agent(self, agent_id):
            return SimpleNamespace(agent_id=agent_id, agent_metadata={})

        async def update_agent(self, agent_id, updates):
            rec.calls.append(("update_agent", updates))

    async def fake_provision(db, **kw):
        if provision_delay:
            await asyncio.sleep(provision_delay)
        rec.calls.append(("provision", kw))
        return SimpleNamespace(agent_id=kw["agent_id"], bootstrap_active=True, warnings=[])

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
        "xyz_agent_context.marketplace.skill_marketplace_service.SkillMarketplaceService",
        FakeSkillSvc,
    )
    monkeypatch.setattr(
        "xyz_agent_context.module.job_module.job_service.JobInstanceService", FakeJobSvc
    )
    monkeypatch.setattr(ob, "is_cloud_mode", lambda: True)


def _enable(monkeypatch):
    monkeypatch.delenv(ob.ENV_FLAG, raising=False)
    monkeypatch.delenv(ob.BACKFILL_ENV_FLAG, raising=False)


def test_kill_switch_disables_everything(monkeypatch):
    rec = Recorder()
    _wire(monkeypatch, rec, users={"u1": {}})
    monkeypatch.setenv(ob.ENV_FLAG, "0")
    res = asyncio.run(ob.ensure_guide_agent(object(), "u1", is_new_user=True))
    assert res == {"skipped": "disabled"}
    assert rec.calls == []  # zero repo/db work when disabled


def test_flag_defaults_are_on(monkeypatch):
    _enable(monkeypatch)
    assert ob.is_guide_agent_enabled() is True
    assert ob.is_backfill_enabled() is True
    monkeypatch.setenv(ob.ENV_FLAG, "false")
    assert ob.is_guide_agent_enabled() is False


def test_backfill_brake_limits_to_new_users(monkeypatch):
    rec = Recorder()
    _wire(monkeypatch, rec, users={"u1": {}})
    monkeypatch.delenv(ob.ENV_FLAG, raising=False)
    monkeypatch.setenv(ob.BACKFILL_ENV_FLAG, "0")
    # Returning (non-new) login: braked before any DB work.
    res = asyncio.run(ob.ensure_guide_agent(object(), "u1", is_new_user=False))
    assert res == {"skipped": "backfill_disabled"}
    assert rec.calls == []
    # Brand-new signup still provisions.
    res = asyncio.run(ob.ensure_guide_agent(object(), "u1", is_new_user=True))
    assert res.get("provisioned") is True


def test_marker_makes_it_idempotent(monkeypatch):
    rec = Recorder()
    _wire(monkeypatch, rec, users={"u1": {ob.GUIDE_METADATA_FLAG: True}})
    _enable(monkeypatch)
    res = asyncio.run(ob.ensure_guide_agent(object(), "u1", is_new_user=True))
    assert res == {"skipped": "already_provisioned"}
    assert all(c[0] != "provision" for c in rec.calls)


def test_missing_user_skips(monkeypatch):
    rec = Recorder()
    _wire(monkeypatch, rec, users={})
    _enable(monkeypatch)
    res = asyncio.run(ob.ensure_guide_agent(object(), "ghost", is_new_user=True))
    assert res == {"skipped": "no_user"}


def test_user_with_agents_gets_marker_but_no_guide(monkeypatch):
    rec = Recorder()
    users = {"u1": {}}
    _wire(monkeypatch, rec, users=users, agents=[SimpleNamespace(agent_id="a1")])
    _enable(monkeypatch)
    res = asyncio.run(ob.ensure_guide_agent(object(), "u1", is_new_user=False))
    assert res == {"skipped": "has_agents"}
    assert all(c[0] != "provision" for c in rec.calls)
    # Marker written so later logins short-circuit before the agents query.
    assert users["u1"][ob.GUIDE_METADATA_FLAG] is True


def test_happy_path_full_sequence_and_claim_first(monkeypatch):
    rec = Recorder()
    users = {"u1": {}}
    _wire(monkeypatch, rec, users=users)
    _enable(monkeypatch)
    res = asyncio.run(ob.ensure_guide_agent(object(), "u1", is_new_user=True))

    assert res["provisioned"] is True and res["warnings"] == []
    steps = [c[0] for c in rec.calls]
    # CLAIM FIRST: the marker write must land BEFORE provisioning starts —
    # that ordering is the whole concurrency defense.
    assert steps.index("update_user") < steps.index("provision")
    assert "install_skill" in steps and "create_job" in steps

    prov_kw = next(kw for name, kw in rec.calls if name == "provision")
    # Single provision call carries the real profile + its render extras —
    # no none-then-reapply window with a blank persisted greeting.
    assert prov_kw["bootstrap_profile"] == "onboarding"
    extra = prov_kw["bootstrap_ctx_extra"]
    assert {"persona_key", "topic_index", "is_local"} <= set(extra)
    assert extra["is_local"] is False  # is_cloud_mode() wired True
    assert any(p["awareness"] in prov_kw["awareness"] for p in PERSONAS)
    assert prov_kw["agent_name"].count("_") >= 2  # three-group random name

    assert ("install_skill", ob.GUIDE_SKILL_ID) in rec.calls

    job_kw = next(kw for name, kw in rec.calls if name == "create_job")
    # SCHEDULED, not ongoing: ongoing's iteration counter + end_condition
    # analysis also fire on every chat event (cost + early COMPLETED).
    assert job_kw["job_type"] == "scheduled"
    tc = job_kw["trigger_config"]
    assert tc == {"interval_seconds": 86400, "timezone": "Asia/Shanghai"}
    assert job_kw["title"] == ob.CHECKIN_JOB_TITLE
    # The payload carries both model-judged exits: 3-strikes and a concrete
    # provision-stamped end date.
    assert "three consecutive" in job_kw["payload"]
    assert "after 20" in job_kw["payload"]  # ISO date literal, e.g. 2026-09-02

    tag = next(kw for name, kw in rec.calls if name == "update_agent")
    assert tag["agent_metadata"]["provisioned_source"] == "onboarding"

    assert users["u1"][ob.GUIDE_METADATA_FLAG] is True


def test_local_mode_flows_into_extras_and_awareness(monkeypatch):
    rec = Recorder()
    _wire(monkeypatch, rec, users={"u1": {}})
    monkeypatch.setattr(ob, "is_cloud_mode", lambda: False)
    _enable(monkeypatch)
    asyncio.run(ob.ensure_guide_agent(object(), "u1", is_new_user=True))
    prov_kw = next(kw for name, kw in rec.calls if name == "provision")
    assert prov_kw["bootstrap_ctx_extra"]["is_local"] is True
    assert "LOCAL INSTALL" in prov_kw["awareness"]


def test_job_failure_is_best_effort_and_marker_still_written(monkeypatch):
    rec = Recorder()
    users = {"u1": {}}
    _wire(monkeypatch, rec, users=users, job_fails=True)
    _enable(monkeypatch)
    res = asyncio.run(ob.ensure_guide_agent(object(), "u1", is_new_user=True))
    assert res["provisioned"] is True
    assert any("checkin_job" in w for w in res["warnings"])
    assert res["job_id"] is None
    assert users["u1"][ob.GUIDE_METADATA_FLAG] is True


def test_marker_write_preserves_sibling_metadata(monkeypatch):
    rec = Recorder()
    users = {"u1": {"onboarding_progress": {"first_agent_created": True}}}
    _wire(monkeypatch, rec, users=users)
    _enable(monkeypatch)
    asyncio.run(ob.ensure_guide_agent(object(), "u1", is_new_user=True))
    meta = users["u1"]
    assert meta["onboarding_progress"] == {"first_agent_created": True}
    assert meta[ob.GUIDE_METADATA_FLAG] is True


def test_concurrent_calls_provision_exactly_once(monkeypatch):
    """Two logins racing (two tabs / client retry): the per-user lock
    serializes them and the second sees the first's marker."""
    rec = Recorder()
    users = {"u1": {}}
    _wire(monkeypatch, rec, users=users, provision_delay=0.01)
    _enable(monkeypatch)

    async def _race():
        return await asyncio.gather(
            ob.ensure_guide_agent(object(), "u1", is_new_user=True),
            ob.ensure_guide_agent(object(), "u1", is_new_user=True),
        )

    r1, r2 = asyncio.run(_race())
    outcomes = sorted(("provisioned" in r1 and "skipped" not in r1,
                       "provisioned" in r2 and "skipped" not in r2))
    assert outcomes == [False, True]
    assert [c[0] for c in rec.calls].count("provision") == 1


def test_safe_timezone_falls_back_on_garbage():
    assert ob._safe_timezone("Asia/Shanghai") == "Asia/Shanghai"
    assert ob._safe_timezone("Not/AZone") == "UTC"
    assert ob._safe_timezone(None) == "UTC"
    assert ob._safe_timezone("") == "UTC"
