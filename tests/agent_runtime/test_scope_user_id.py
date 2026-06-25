"""
@file_name: test_scope_user_id.py
@author: NetMind.AI
@date: 2026-06-24
@description: IM identity-tenant — _resolve_scope_user_id decides the per-run scope
identity (narrative / workspace / memory partition) from the IDENTITY itself.

An external subject (ext_…) keeps its own scope — so each external IM conversation
is its own tenant, and DERIVED work (a job an external user creates) stays external
automatically (its stored user_id is the ext_ subject). Any other user_id collapses
to the agent owner — the historical "all triggers share one space" behavior. Billing
is unaffected (resolved off agent_id, never this value).
"""
from xyz_agent_context.agent_runtime.agent_runtime import _resolve_scope_user_id


def test_real_user_collapses_to_owner():
    # web / job / message-bus with a real user_id → collapse to owner (historical).
    assert _resolve_scope_user_id("someone", "owner_x") == "owner_x"


def test_real_user_falls_back_when_no_creator():
    assert _resolve_scope_user_id("someone", None) == "someone"


def test_external_subject_keeps_its_scope():
    # External IM turn — and any job/callback carrying the same ext_ subject.
    assert _resolve_scope_user_id("ext_slack_room1", "owner_x") == "ext_slack_room1"


def test_external_subject_kept_even_without_creator():
    assert _resolve_scope_user_id("ext_slack_room1", None) == "ext_slack_room1"


def test_external_job_propagation_is_automatic():
    # A job created by an external user stores user_id=ext_... ; job_trigger later
    # runs it with that value → scope stays external WITHOUT any flag.
    job_user_id = "ext_narramessenger_abc123def456"
    assert _resolve_scope_user_id(job_user_id, "owner_x") == job_user_id
