"""
@file_name: test_runtime_policy.py
@author: NetMind.AI
@date: 2026-06-24
@description: T1 — RuntimePolicy abstraction + RunContext.policy field +
StaticVisitorRuntime subclass.

Verifies the policy plumbing that lets a distrust (external IM visitor) turn
run a behaviorally-restricted variant WITHOUT touching the owner-facing path:
the base AgentRuntime defaults to OWNER_POLICY (all flags permissive == current
behavior), and StaticVisitorRuntime carries STATIC_VISITOR_POLICY.
"""
from dataclasses import fields

import pytest

from xyz_agent_context.agent_runtime.runtime_policy import (
    RuntimePolicy,
    OWNER_POLICY,
    STATIC_VISITOR_POLICY,
)
from xyz_agent_context.agent_runtime._agent_runtime_steps.context import RunContext
from xyz_agent_context.agent_runtime.agent_runtime import AgentRuntime
from xyz_agent_context.agent_runtime.static_visitor_runtime import StaticVisitorRuntime


# ---- The policy object itself --------------------------------------------

def test_owner_policy_is_fully_permissive():
    """OWNER_POLICY must equal current behavior: every restriction OFF.

    This is the zero-regression guard — any field defaulting to a restrictive
    value would silently change the owner-facing path.
    """
    assert OWNER_POLICY.skip_after_execution_hooks is False
    assert OWNER_POLICY.scrub_provider_env is False
    assert OWNER_POLICY.scrub_internal_ids is False
    assert OWNER_POLICY.workspace_mode == "owner"
    assert OWNER_POLICY.block_owner_path_writes is False
    assert OWNER_POLICY.im_short_term is False


def test_runtime_policy_defaults_match_owner_policy():
    """A bare RuntimePolicy() must be permissive — defaults are the owner path."""
    bare = RuntimePolicy()
    for f in fields(RuntimePolicy):
        assert getattr(bare, f.name) == getattr(OWNER_POLICY, f.name)


def test_static_visitor_policy_v1_enforced_flags_on():
    """The flags v1 actually enforces: skip hooks, scratch workspace, IM short-term."""
    assert STATIC_VISITOR_POLICY.skip_after_execution_hooks is True
    assert STATIC_VISITOR_POLICY.workspace_mode == "scratch"
    assert STATIC_VISITOR_POLICY.im_short_term is True


def test_static_visitor_policy_v2_deferred_flags_off():
    """v2-deferred flags must be OFF in v1 — the policy can't claim unenforced protection.

    provider-env scrub / internal-id scrub / owner-path write block all need the v2
    sandbox (credential proxy / OS isolation), so they stay off until then.
    """
    assert STATIC_VISITOR_POLICY.scrub_provider_env is False
    assert STATIC_VISITOR_POLICY.scrub_internal_ids is False
    assert STATIC_VISITOR_POLICY.block_owner_path_writes is False


def test_runtime_policy_is_frozen():
    """Module-level singletons must be immutable so callers can't mutate shared state."""
    import dataclasses
    import pytest

    with pytest.raises(dataclasses.FrozenInstanceError):
        OWNER_POLICY.skip_after_execution_hooks = True


# ---- RunContext carries the policy ---------------------------------------

def _ctx(**kw):
    base = dict(agent_id="a", user_id="u", input_content="hi", working_source="chat")
    base.update(kw)
    return RunContext(**base)


def test_runcontext_defaults_to_owner_policy():
    assert _ctx().policy is OWNER_POLICY


def test_runcontext_accepts_policy_override():
    assert _ctx(policy=STATIC_VISITOR_POLICY).policy is STATIC_VISITOR_POLICY


# ---- Runtime subclass wiring ---------------------------------------------

def test_base_runtime_uses_owner_policy():
    assert AgentRuntime()._policy is OWNER_POLICY


def test_static_visitor_runtime_is_agent_runtime_subclass():
    assert issubclass(StaticVisitorRuntime, AgentRuntime)


def test_static_visitor_runtime_stores_policy():
    rt = StaticVisitorRuntime()
    assert rt._policy is STATIC_VISITOR_POLICY


def test_static_visitor_runtime_accepts_custom_policy():
    custom = RuntimePolicy(skip_after_execution_hooks=True)
    rt = StaticVisitorRuntime(policy=custom)
    assert rt._policy is custom


@pytest.mark.asyncio
async def test_static_visitor_run_and_collect_drives_self(monkeypatch):
    """run_and_collect drives collect_run with the StaticVisitorRuntime instance
    itself (so the distrust policy is carried), under the admission gate."""
    captured = {}

    async def fake_collect_run(runtime, **kw):
        captured["runtime"] = runtime
        captured["kw"] = kw
        return "RESULT"

    class _Slot:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *a):
            return False

    class _Ctrl:
        def slot(self, user_id):
            captured["slot_user"] = user_id
            return _Slot()

    monkeypatch.setattr(
        "xyz_agent_context.agent_runtime.run_collector.collect_run", fake_collect_run
    )
    monkeypatch.setattr(
        "xyz_agent_context.agent_runtime.admission.get_admission_controller",
        lambda: _Ctrl(),
    )

    rt = StaticVisitorRuntime()
    out = await rt.run_and_collect(
        agent_id="a", user_id="owner", input_content="hi",
        working_source="chat", trigger_extra_data={"im_room_id": "r1"},
    )

    assert out == "RESULT"
    assert captured["runtime"] is rt
    assert captured["runtime"]._policy.skip_after_execution_hooks is True
    assert captured["slot_user"] == "owner"
    assert captured["kw"]["agent_id"] == "a"
    assert captured["kw"]["trigger_extra_data"] == {"im_room_id": "r1"}
