"""
@file_name: test_dashboard_route.py
@description: T17 — main /api/dashboard/agents-status integration tests.
"""
import pytest
from fastapi.testclient import TestClient


def test_rejects_user_id_query_param(local_client_seeded):
    r = local_client_seeded["client"].get("/api/dashboard/agents-status?user_id=X")
    assert r.status_code == 400
    assert "user_id" in r.text.lower()


def test_returns_owned_and_public_split(local_client_seeded):
    ctx = local_client_seeded["ctx"]
    r = local_client_seeded["client"].get("/api/dashboard/agents-status")
    assert r.status_code == 200, r.text
    data = r.json()
    ids = [a["agent_id"] for a in data["agents"]]
    # alice should see: a1 (owned private), a2 (owned public), b1 (public non-owned)
    # b2 (private non-owned) MUST NOT appear
    assert ctx["a1"] in ids
    assert ctx["a2"] in ids
    assert ctx["b1"] in ids
    assert ctx["b2"] not in ids
    # b1 is public non-owned → has no action_line / sessions / running_count
    b1 = next(a for a in data["agents"] if a["agent_id"] == ctx["b1"])
    assert b1["owned_by_viewer"] is False
    assert "action_line" not in b1
    assert "sessions" not in b1
    assert "running_count" not in b1
    assert "running_count_bucket" in b1


def test_private_non_owned_filtered_out(local_client_seeded):
    """G008 acceptance criterion 3: private non-owned agents absent from response."""
    ctx = local_client_seeded["ctx"]
    r = local_client_seeded["client"].get("/api/dashboard/agents-status")
    data = r.json()
    assert all(a["agent_id"] != ctx["b2"] for a in data["agents"])


def test_rate_limit_returns_429_on_burst(local_client_seeded, monkeypatch):
    """Rate limiter: 2 req/sec. Because test DB is slow, we patch the
    limiter's window_sec to a large value so that multiple sequential test
    requests still fall within one logical 'window'."""
    from backend.routes import dashboard as dash_mod

    monkeypatch.setattr(dash_mod._rate_limiter, "_window", 3600.0)  # 1-hour window
    client = local_client_seeded["client"]
    statuses = []
    for _ in range(3):
        r = client.get("/api/dashboard/agents-status")
        statuses.append(r.status_code)
    assert 429 in statuses, f"expected a 429 in {statuses}"
    # Any 429 response carries Retry-After
    for r_status in statuses:
        if r_status == 429:
            break
    # Request again (still in window) → 429 + Retry-After header
    again = client.get("/api/dashboard/agents-status")
    assert again.status_code == 429
    assert "Retry-After" in again.headers or "retry-after" in (h.lower() for h in again.headers.keys())
