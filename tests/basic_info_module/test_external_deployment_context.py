"""
@file_name: test_external_deployment_context.py
@author: NetMind.AI
@date: 2026-06-25
@description: IM identity-tenant (A) — select_deployment_context routes an external
IM subject to the strict EXTERNAL_IM prompt block, overriding cloud/local.

The key property: an external subject on a LOCAL deployment must NOT get the
relaxed LOCAL block (the external user is not the machine owner) — it gets the
strict EXTERNAL block. Real users are unaffected (cloud→CLOUD, local→LOCAL).
"""
from xyz_agent_context.module.basic_info_module.prompts import (
    DEPLOYMENT_CONTEXT_CLOUD,
    DEPLOYMENT_CONTEXT_EXTERNAL_IM,
    DEPLOYMENT_CONTEXT_LOCAL,
    select_deployment_context,
)


def test_real_user_cloud_gets_cloud():
    assert select_deployment_context("cloud", "alice") == DEPLOYMENT_CONTEXT_CLOUD


def test_real_user_local_gets_local():
    assert select_deployment_context("local", "alice") == DEPLOYMENT_CONTEXT_LOCAL


def test_external_subject_overrides_local():
    # The crucial interaction: external on a local box is STILL strict.
    assert (
        select_deployment_context("local", "ext_slack_room1")
        == DEPLOYMENT_CONTEXT_EXTERNAL_IM
    )


def test_external_subject_on_cloud_is_external():
    assert (
        select_deployment_context("cloud", "ext_narramessenger_abc123")
        == DEPLOYMENT_CONTEXT_EXTERNAL_IM
    )


def test_external_block_is_strict():
    # Sanity: the external block must NOT carry the relaxed-local language.
    assert "strict" in DEPLOYMENT_CONTEXT_EXTERNAL_IM.lower()
    assert "MUST stay" in DEPLOYMENT_CONTEXT_EXTERNAL_IM


def test_external_owner_shared_path_appended():
    # B-4: the owner's read-only workspace path is surfaced to an external subject.
    out = select_deployment_context(
        "local", "ext_slack_room1", owner_shared_path="/ws/owner1/agent_x"
    )
    assert "/ws/owner1/agent_x" in out
    assert "READ-ONLY" in out
    assert "NEVER write" in out


def test_owner_path_ignored_for_real_user():
    # A real user never gets the external block, path or not.
    out = select_deployment_context("local", "alice", owner_shared_path="/ws/owner1/x")
    assert out == DEPLOYMENT_CONTEXT_LOCAL
    assert "/ws/owner1/x" not in out
