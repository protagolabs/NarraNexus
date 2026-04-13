"""
@file_name: test_dashboard_permission.py
@description: T17 — G008 field whitelist enforcement on public-variant responses.
"""
import pytest


PUBLIC_WHITELIST = {
    "agent_id", "name", "description",
    "is_public", "owned_by_viewer",
    "status", "running_count_bucket",
}
STATUS_COMMON_WHITELIST = {"kind", "last_activity_at", "started_at"}


def test_public_agent_field_whitelist_strict_equality(local_client_seeded):
    r = local_client_seeded["client"].get("/api/dashboard/agents-status")
    assert r.status_code == 200
    public_agents = [a for a in r.json()["agents"] if a["owned_by_viewer"] is False]
    assert public_agents, "fixture must include at least one public non-owned"
    for a in public_agents:
        extra = set(a.keys()) - PUBLIC_WHITELIST
        assert not extra, f"Public variant leaks fields: {extra}"
        missing = PUBLIC_WHITELIST - set(a.keys())
        assert not missing, f"Public variant missing required fields: {missing}"


def test_status_subobject_on_public_has_no_details(local_client_seeded):
    r = local_client_seeded["client"].get("/api/dashboard/agents-status")
    public_agents = [a for a in r.json()["agents"] if a["owned_by_viewer"] is False]
    for a in public_agents:
        assert "details" not in a["status"]
        assert set(a["status"].keys()) <= STATUS_COMMON_WHITELIST


def test_owned_agent_has_full_fields(local_client_seeded):
    r = local_client_seeded["client"].get("/api/dashboard/agents-status")
    owned = [a for a in r.json()["agents"] if a["owned_by_viewer"] is True]
    assert owned, "fixture must include owned agents"
    for a in owned:
        assert "sessions" in a
        assert "running_jobs" in a
        assert "pending_jobs" in a
        assert "running_count" in a
        assert "action_line" in a
        assert "enhanced" in a
