"""
@file_name: test_user_settings_routes.py
@date: 2026-06-08
@description: GET/PUT analytics opt-out round-trips via the API.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


async def _async_return(v):
    return v


@pytest.fixture
def client(db_client, monkeypatch):
    import backend.routes.auth as auth_mod
    monkeypatch.setattr(auth_mod, "get_db_client", lambda: _async_return(db_client))
    monkeypatch.setattr(auth_mod, "_is_cloud_mode", lambda: False)
    app = FastAPI()
    app.include_router(auth_mod.router, prefix="/api/auth")
    return TestClient(app)


def test_opt_out_defaults_false_then_toggles(client):
    g = client.get("/api/auth/settings/analytics", params={"user_id": "u1"})
    assert g.status_code == 200 and g.json()["opted_out"] is False
    p = client.put("/api/auth/settings/analytics",
                   json={"user_id": "u1", "opted_out": True})
    assert p.status_code == 200
    g2 = client.get("/api/auth/settings/analytics", params={"user_id": "u1"})
    assert g2.json()["opted_out"] is True
