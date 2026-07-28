"""
Agent Migration routes — local-only gate + scan/detect surface.
"""
from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.routes.migrate as mig


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(mig.router, prefix="/api/migrate")
    return TestClient(app)


def _mk_claude(home):
    d = home / ".claude"
    d.mkdir(parents=True)
    (d / "CLAUDE.md").write_text("You are a Claude Code agent.", encoding="utf-8")
    (d / ".mcp.json").write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    return d


def test_scan_blocked_on_cloud(tmp_path, monkeypatch):
    monkeypatch.setattr(mig, "is_cloud_mode", lambda: True)
    r = _client().post("/api/migrate/scan", json={"path": str(_mk_claude(tmp_path))})
    assert r.status_code == 503
    assert "migration_local_only" in r.json()["detail"]


def test_detect_blocked_on_cloud(monkeypatch):
    monkeypatch.setattr(mig, "is_cloud_mode", lambda: True)
    assert _client().get("/api/migrate/detect").status_code == 503


def test_scan_local_returns_standardized_json(tmp_path, monkeypatch):
    monkeypatch.setattr(mig, "is_cloud_mode", lambda: False)
    d = _mk_claude(tmp_path)
    r = _client().post("/api/migrate/scan", json={"path": str(d)})
    assert r.status_code == 200
    body = r.json()
    assert body["schema_version"] == "1.0"
    assert body["source"]["framework"] == "claude_code"
    assert "Claude Code agent" in body["agent"]["system_prompt"]


def test_scan_missing_source_404(tmp_path, monkeypatch):
    monkeypatch.setattr(mig, "is_cloud_mode", lambda: False)
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: tmp_path))
    r = _client().post("/api/migrate/scan", json={})  # empty home, no path
    assert r.status_code == 404
