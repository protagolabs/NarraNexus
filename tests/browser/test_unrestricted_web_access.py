"""
@file_name: test_unrestricted_web_access.py
@date: 2026-09-23
@description: Retired website rules cannot block browsing or reappear as approvals.
"""
import json

import pytest

from narranexus.platform.browser._browser_impl.approval_store import ApprovalStore
from narranexus.platform.browser._browser_impl.policy import BrowserPolicy
from narranexus.platform.browser._browser_impl.policy_store import PolicyStore


@pytest.mark.asyncio
async def test_legacy_access_rules_are_absent_from_policy_and_public_view(db_client):
    document = {
        "default_origin_policy": {"access": "deny"},
        "origins": {
            "https://blocked.example": {"access": "deny"},
            "https://scripts.example": {"access": "ask", "full_cdp_access": "allow"},
        },
        "grants": [["https://allowed.example", "access", "thread:old"]],
        "denials": [["https://blocked.example", "access", "turn:old"]],
    }
    await db_client.insert("instance_browser_policies", {"agent_id": "agent", "policy_json": json.dumps(document)})
    policy = BrowserPolicy.from_dict(document).to_dict()
    assert "access" not in policy["default_origin_policy"]
    assert "https://blocked.example" not in policy["origins"]
    assert policy["grants"] == policy["denials"] == []
    assert await PolicyStore(db_client).get_public("agent") == {
        "agent_id": "agent", "defaults": {"full_cdp_access": "deny"},
        "origins": [{"origin": "https://scripts.example", "full_cdp_access": "allow"}],
    }


@pytest.mark.asyncio
async def test_old_access_prompts_cannot_be_listed_or_answered(db_client):
    await db_client.insert("instance_browser_approvals", {
        "approval_id": "old_access", "agent_id": "agent", "origin": "https://site.example",
        "capability": "access", "turn_id": "turn", "thread_id": "thread", "requested_at": "2026-09-22T00:00:00",
    })
    store = ApprovalStore(db_client)
    assert await store.pending("agent") == []
    assert await store.get("old_access") is None
    assert not await store.resolve("old_access", agent_id="agent", decision="deny", lifetime="always")
    assert await db_client.get("instance_browser_policies", {"agent_id": "agent"}) == []


@pytest.mark.asyncio
async def test_access_rules_and_prompts_can_no_longer_be_created(db_client):
    with pytest.raises(ValueError):
        await PolicyStore(db_client).set_rule("agent", origin="https://site.example", capability="access", verdict="deny")
    with pytest.raises(ValueError):
        await ApprovalStore(db_client).request(agent_id="agent", origin="https://site.example", capability="access",
                                               turn_id="turn", thread_id="thread")
    assert await db_client.get("instance_browser_approvals", {"agent_id": "agent"}) == []
