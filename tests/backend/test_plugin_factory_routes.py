"""
@file_name: test_plugin_factory_routes.py
@author: Bin Liang
@date: 2026-09-03
@description: /api/plugin-factory: list/install/enable/disable/rollback/bisect/errors/assets over a temp plugin home; cloud mutations 403; traversal refused.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from narranexus.kernel.plugins.install import Installer
from narranexus.kernel.plugins.lifecycle import RegistryStore
from narranexus.kernel.plugins.paths import ENV_PLUGIN_HOME
from backend.plugins_factory import routes as factory_routes
from backend.plugins_factory.service import FactoryService

H = {"X-User-Id": "u1"}


def _plugin(root: Path, pid="acme.weather"):
    root.mkdir(parents=True, exist_ok=True)
    (root / "narranexus-plugin.json").write_text(json.dumps({"id": pid, "version": "1.0.0", "displayName": "Weather", "hosts": ["backend"], "backend": {"activate": True}, "permissions": {"network": ["api.weather.com"]}}))
    (root / "backend").mkdir(exist_ok=True)
    (root / "backend" / "__init__.py").write_text("def activate(ctx):\n    pass\n")
    (root / "frontend" / "dist").mkdir(parents=True, exist_ok=True)
    (root / "frontend" / "dist" / "plugin.js").write_text("export const plugin = 1;")
    return root


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv(ENV_PLUGIN_HOME, str(home))
    store = RegistryStore(path=home / "registry.json", lkg=home / "lkg.json")
    svc = FactoryService(store=store, installer=Installer(store=store, host="1.19.0"))
    factory_routes.set_service(svc)
    from backend.auth import auth_middleware

    app = FastAPI()
    app.middleware("http")(auth_middleware)
    app.include_router(factory_routes.router)
    yield TestClient(app), svc, home
    factory_routes.set_service(None)


def test_install_list_enable_disable_ack_uninstall(client, tmp_path: Path):
    c, svc, home = client
    assert c.get("/api/plugin-factory").status_code == 401  # fail-closed
    src = _plugin(tmp_path / "dev" / "acme.weather")
    r = c.post("/api/plugin-factory/install", json={"source": str(src)}, headers=H)
    assert r.status_code == 200 and r.json()["data"]["id"] == "acme.weather" and r.json()["data"]["permissions"]["network"] == ["api.weather.com"]
    data = c.get("/api/plugin-factory", headers=H).json()["data"]
    (row,) = data["plugins"]
    assert row["display_name"] == "Weather" and row["enabled"] and row["state"] == "registered" and row["provides"] == []
    assert data["safe_mode"] is False and data["cloud_managed"] is False
    assert c.post("/api/plugin-factory/acme.weather/disable", headers=H).json()["data"]["enabled"] is False
    assert c.post("/api/plugin-factory/acme.weather/enable", headers=H).json()["data"]["state"] == "registered"
    assert c.post("/api/plugin-factory/acme.weather/acknowledge-permissions", headers=H).json()["data"]["permissions_acknowledged"] is True
    assert c.post("/api/plugin-factory/nope/enable", headers=H).status_code == 404
    assert c.post("/api/plugin-factory/acme.weather/uninstall", headers=H).json()["data"]["purged"] is True
    assert c.get("/api/plugin-factory", headers=H).json()["data"]["plugins"] == []


def test_rollback_bisect_and_errors(client, tmp_path: Path):
    c, svc, home = client
    for pid in ("acme.a", "acme.b", "acme.c"):
        c.post("/api/plugin-factory/install", json={"source": str(_plugin(tmp_path / pid, pid))}, headers=H)
    c.post("/api/plugin-factory/acme.c/uninstall", headers=H)
    assert sorted(c.post("/api/plugin-factory/rollback", headers=H).json()["data"]["plugins"]) == ["acme.a", "acme.b", "acme.c"]
    step = c.post("/api/plugin-factory/bisect/start", headers=H).json()["data"]
    assert step["remaining"] == 3 and len(step["trial"]) == 1
    assert c.get("/api/plugin-factory", headers=H).json()["data"]["bisect"] is not None
    step = c.post("/api/plugin-factory/bisect/answer", json={"good": True}, headers=H).json()["data"]
    assert step["remaining"] == 2
    assert c.post("/api/plugin-factory/bisect/stop", headers=H).json()["data"]["stopped"]
    assert c.post("/api/plugin-factory/acme.a/errors", json={"kind": "render", "message": "boom", "stack": "at x"}, headers=H).json()["data"]["count"] == 1
    assert c.get("/api/plugin-factory/acme.a/errors", headers=H).json()["data"]["errors"][0]["message"] == "boom"
    assert c.get("/api/plugin-factory", headers=H).json()["data"]["plugins"][0]["recent_errors"] == 1
    assert c.post("/api/plugin-factory/safe-mode/leave", headers=H).json()["data"]["safe_mode"] is False


def test_assets_are_served_with_sri_and_traversal_is_refused(client, tmp_path: Path):
    c, svc, home = client
    c.post("/api/plugin-factory/install", json={"source": str(_plugin(tmp_path / "dev" / "acme.weather"))}, headers=H)
    r = c.get("/api/plugin-factory/acme.weather/assets/plugin.js", headers=H)
    assert r.status_code == 200 and r.headers["x-content-integrity"].startswith("sha256-") and "export" in r.text
    assert c.get("/api/plugin-factory/acme.weather/assets/../narranexus-plugin.json", headers=H).status_code in (400, 404)
    assert c.get("/api/plugin-factory/acme.weather/assets/%2e%2e/narranexus-plugin.json", headers=H).status_code == 400
    assert c.get("/api/plugin-factory/acme.weather/assets/missing.js", headers=H).status_code == 404
    assert c.get("/api/plugin-factory/nope/assets/plugin.js", headers=H).status_code == 404


def test_cloud_mode_refuses_mutations_but_lists(client, monkeypatch, tmp_path: Path):
    c, svc, home = client
    monkeypatch.setattr("backend.plugins_factory.service.is_cloud_mode", lambda: True)
    assert c.get("/api/plugin-factory", headers=H).json()["data"]["cloud_managed"] is True
    assert c.post("/api/plugin-factory/install", json={"source": str(tmp_path)}, headers=H).status_code == 403
    assert c.post("/api/plugin-factory/x/enable", headers=H).status_code == 403
    assert c.post("/api/plugin-factory/bisect/start", headers=H).status_code == 403


def test_main_app_mounts_the_factory_router():
    import backend.main as main

    paths = {getattr(r, "path", "") for r in main.app.routes}
    assert "/api/plugin-factory" in paths and "/api/plugin-factory/install" in paths
