"""
@file_name: test_service.py
@author: NarraNexus
@date: 2026-08-28
@description: Tests for PluginService — the orchestration facade Phase 3
              routes will consume. Installers are stubbed so no real
              pip/npm process runs.
"""
from __future__ import annotations

import asyncio

import pytest

from backend.integrations.plugins.service import PluginService
from backend.integrations.plugins.spec import InstallComponent, PluginSpec


@pytest.fixture()
def plugin_home(tmp_path, monkeypatch):
    monkeypatch.setenv("NARRANEXUS_PLUGIN_HOME", str(tmp_path))
    return tmp_path


_TEST_SPECS = {
    "claude_code": PluginSpec(
        id="claude_code",
        display_name="Claude Code",
        framework_name="claude_code",
        components=(
            InstallComponent(kind="pip", requirement="claude-agent-sdk==0.1.43"),
            InstallComponent(kind="npm", requirement="@anthropic-ai/claude-code@2.1.220"),
        ),
        probe_package="claude_agent_sdk",
        user_version_source="npm_cli",
        size_hint="~190 MB",
    ),
}


class _StubInstaller:
    """Deterministic stand-in for PipTargetInstaller/NpmPrefixInstaller."""

    def __init__(self, lines, installed_after=True, version_after="2.1.220"):
        self._lines = lines
        self._installed_after = installed_after
        self._version_after = version_after
        self.uninstalled_components = []

    async def install(self, component):
        for line in self._lines:
            await asyncio.sleep(0)
            yield line

    def detect(self, component):
        from backend.integrations.plugins._installers.base import InstalledState

        return InstalledState(
            installed=self._installed_after,
            version=self._version_after if self._installed_after else None,
            target_version="2.1.220",
            update_available=False,
        )

    async def uninstall(self, component):
        self.uninstalled_components.append(component)


def _service_with_stub_installers(**kwargs):
    service = PluginService(specs=_TEST_SPECS)
    pip_installer = _StubInstaller(["collecting...", "done"], **kwargs)
    npm_installer = _StubInstaller(["npm install..."], **kwargs)
    service._installers["pip"] = pip_installer
    service._installers["npm"] = npm_installer
    return service, pip_installer, npm_installer


def test_unknown_plugin_id_raises_key_error(plugin_home):
    service = PluginService(specs=_TEST_SPECS)
    with pytest.raises(KeyError):
        service._spec("does_not_exist")


def test_list_plugins_reports_not_installed_by_default(plugin_home):
    service, _, _ = _service_with_stub_installers(installed_after=False)
    statuses = service.list_plugins()
    assert len(statuses) == 1
    status = statuses[0]
    assert status.id == "claude_code"
    assert status.installed is False
    assert status.busy is False


def test_list_plugins_reports_installed_when_all_components_present(plugin_home):
    service, _, _ = _service_with_stub_installers(installed_after=True, version_after="2.1.220")
    status = service.list_plugins()[0]
    assert status.installed is True
    assert status.version == "2.1.220"


@pytest.mark.asyncio
async def test_install_yields_progress_lines_then_a_done_event(plugin_home):
    service, _, _ = _service_with_stub_installers(installed_after=True)
    events = [event async for event in service.install("claude_code")]

    assert events[0] == {"phase": "pip", "line": "collecting...", "done": False}
    assert events[1] == {"phase": "pip", "line": "done", "done": False}
    assert events[2] == {"phase": "npm", "line": "npm install...", "done": False}

    final = events[-1]
    assert final["done"] is True
    assert final["ok"] is True
    assert final["error"] is None
    assert final["status"]["id"] == "claude_code"
    assert final["status"]["installed"] is True


@pytest.mark.asyncio
async def test_install_reports_logged_in_files(plugin_home, monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("backend.integrations.plugins.service.Path.home", lambda: home)

    service, _, _ = _service_with_stub_installers(installed_after=True)
    assert service.list_plugins()[0].logged_in is False

    creds_dir = home / ".claude"
    creds_dir.mkdir()
    (creds_dir / ".credentials.json").write_text("{}")
    assert service.list_plugins()[0].logged_in is True


@pytest.mark.asyncio
async def test_concurrent_install_is_rejected_while_busy(plugin_home):
    service, _, _ = _service_with_stub_installers(installed_after=True)

    gen = service.install("claude_code")
    first_event = await gen.__anext__()
    assert first_event["done"] is False

    # A second install call while the first is still mid-flight must be
    # rejected outright, not queued behind the lock.
    statuses_while_busy = service.list_plugins()
    assert statuses_while_busy[0].busy is True

    second_events = [event async for event in service.install("claude_code")]
    assert second_events == [
        {"done": True, "ok": False, "error": second_events[0]["error"], "status": None}
    ]
    assert second_events[0]["error"]

    # Drain the first install so the lock is released cleanly.
    async for _ in gen:
        pass

    assert service.list_plugins()[0].busy is False


@pytest.mark.asyncio
async def test_uninstall_calls_uninstall_on_every_component(plugin_home):
    service, pip_installer, npm_installer = _service_with_stub_installers(installed_after=True)
    await service.uninstall("claude_code")
    assert len(pip_installer.uninstalled_components) == 1
    assert len(npm_installer.uninstalled_components) == 1
