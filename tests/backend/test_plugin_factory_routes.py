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
    assert row["frontend"] is None and row["activation_events"] == ["onStartup"] and row["protected"] is False
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


def test_proposals_are_listed_and_decided_by_the_user(client, tmp_path: Path, monkeypatch):
    c, svc, home = client
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "local")
    from narranexus.kernel.plugins.install import Installer, LocalSource
    from narranexus.platform.module_system.nexus_plugins_module._nexus_plugins_impl.state import ProposalStore

    src = _plugin(tmp_path / "dev" / "acme.weather")
    Installer(store=svc.store, host="1.19.0").install(LocalSource(src, mode="link"), installed_by="agent:a1", scope="agent:a1")
    svc.store.set_enabled("acme.weather", False)
    p = ProposalStore().create(plugin_id="acme.weather", agent_id="a1", user_id="u1", action="activate", scope="agent", summary="Activate", permissions={"network": ["x"]}, test_report={"ok": True}, diff_hash="h")
    listed = c.get("/api/plugin-factory/proposals", headers=H).json()["data"]["proposals"]
    assert [x["id"] for x in listed] == [p.id] and listed[0]["permissions"] == {"network": ["x"]}
    r = c.post(f"/api/plugin-factory/proposals/{p.id}/decide", json={"approved": True}, headers=H)
    assert r.status_code == 200 and r.json()["data"]["decision"] == "approved"
    rec = svc.store.read().plugins["acme.weather"]
    assert rec.enabled and rec.scope == "agent:a1" and rec.permissions_acknowledged
    assert c.get("/api/plugin-factory/proposals", headers=H).json()["data"]["proposals"] == []
    assert c.post(f"/api/plugin-factory/proposals/{p.id}/decide", json={"approved": False}, headers=H).status_code == 400  # already decided
    assert c.post("/api/plugin-factory/proposals/prop_nope/decide", json={"approved": True}, headers=H).status_code == 404


def test_builtin_rows_and_toggle(client):
    c, svc, home = client
    data = c.get("/api/plugin-factory", headers=H).json()["data"]
    rows = {b["id"]: b for b in data["builtins"]}
    assert rows["builtin.teams"]["enabled"] and not rows["builtin.teams"]["protected"]
    assert rows["builtin.nexus_plugins_module"]["protected"]
    r = c.post("/api/plugin-factory/builtin/builtin.teams/disable", headers=H)
    assert r.status_code == 200 and r.json()["data"]["enabled"] is False and r.json()["data"]["restart_required"]
    reg = json.loads((home / "registry.json").read_text())
    assert reg["builtin_overrides"]["builtin.teams"] == {"enabled": False}
    data = c.get("/api/plugin-factory", headers=H).json()["data"]
    assert {b["id"]: b["enabled"] for b in data["builtins"]}["builtin.teams"] is False
    assert c.post("/api/plugin-factory/builtin/builtin.teams/enable", headers=H).status_code == 200
    assert "builtin.teams" not in json.loads((home / "registry.json").read_text())["builtin_overrides"]


def test_builtin_toggle_refuses_protected_and_unknown(client):
    c, _, home = client
    assert c.post("/api/plugin-factory/builtin/builtin.nexus_plugins_module/disable", headers=H).status_code >= 400
    reg_file = home / "registry.json"
    assert not reg_file.exists() or not json.loads(reg_file.read_text()).get("builtin_overrides")
    assert c.post("/api/plugin-factory/builtin/acme.nope/disable", headers=H).status_code == 404
    assert c.post("/api/plugin-factory/builtin/builtin.nexus_plugins_module/enable", headers=H).status_code == 200


def test_disabling_a_builtin_cascades_to_its_dependants(client):
    c, _, home = client
    r = c.post("/api/plugin-factory/builtin/builtin.message_bus/disable", headers=H)
    assert r.status_code == 200 and "builtin.teams" in r.json()["data"]["also_disabled"]
    overrides = json.loads((home / "registry.json").read_text())["builtin_overrides"]
    assert overrides["builtin.teams"] == {"enabled": False, "because": "builtin.message_bus"}


def test_builtin_install_deps_retry(client, monkeypatch):
    from narranexus.kernel.plugins.install import builtin_deps

    c, svc, home = client
    # lark's SDK is present here, so the retry is a no-op success.
    r = c.post("/api/plugin-factory/builtin/builtin.channels.lark/install-deps", headers=H)
    assert r.status_code == 200 and r.json()["data"]["restart_required"]
    # a builtin without on-demand deps is a 400; unknown is a 404
    assert c.post("/api/plugin-factory/builtin/builtin.teams/install-deps", headers=H).status_code == 400
    assert c.post("/api/plugin-factory/builtin/acme.nope/install-deps", headers=H).status_code == 404
    rows = {b["id"]: b for b in c.get("/api/plugin-factory", headers=H).json()["data"]["builtins"]}
    assert rows["builtin.channels.lark"]["on_demand"] and rows["builtin.channels.lark"]["pip"] == ["lark-oapi>=1.4.0,<2.0.0"]
    assert rows["builtin.teams"]["on_demand"] is False and rows["builtin.teams"]["deps_missing"] is None
    assert builtin_deps.is_on_demand
