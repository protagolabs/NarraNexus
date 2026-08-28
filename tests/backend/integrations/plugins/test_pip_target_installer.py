"""
@file_name: test_pip_target_installer.py
@author: NarraNexus
@date: 2026-08-28
@description: Tests for PipTargetInstaller — the ``sys.executable -m pip
              install --target <pyenv>`` strategy. Uses a fake subprocess so
              no real network/pip call happens.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from backend.integrations.plugins._installers.base import PluginInstallSubprocessError
from backend.integrations.plugins._installers.pip_target import PipTargetInstaller
from backend.integrations.plugins.spec import InstallComponent


class _FakeStdout:
    def __init__(self, lines: list[bytes]):
        self._lines = list(lines)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._lines:
            raise StopAsyncIteration
        return self._lines.pop(0)


class _FakeProcess:
    def __init__(self, lines: list[bytes], returncode: int = 0):
        self.stdout = _FakeStdout(lines)
        self._returncode = returncode

    async def wait(self):
        return self._returncode


@pytest.fixture()
def plugin_home(tmp_path, monkeypatch):
    monkeypatch.setenv("NARRANEXUS_PLUGIN_HOME", str(tmp_path))
    return tmp_path


def _write_fake_dist(pyenv_dir: Path, import_name: str, dist_prefix: str, version: str) -> None:
    (pyenv_dir / import_name).mkdir(parents=True, exist_ok=True)
    dist_info = pyenv_dir / f"{dist_prefix}-{version}.dist-info"
    dist_info.mkdir(parents=True, exist_ok=True)
    (dist_info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {dist_prefix}\nVersion: {version}\n")


@pytest.mark.asyncio
async def test_install_invokes_pip_with_target_and_pinned_requirement(plugin_home, monkeypatch):
    from backend.integrations.plugins import _installers as installers_pkg  # noqa: F401
    from xyz_agent_context.agent_framework.plugin_paths import pyenv_dir

    captured_cmd: list[str] = []

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured_cmd.extend(cmd)
        return _FakeProcess([b"Collecting claude-agent-sdk==0.1.43\n", b"Successfully installed\n"])

    monkeypatch.setattr(
        "backend.integrations.plugins._installers.base.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    installer = PipTargetInstaller()
    component = InstallComponent(kind="pip", requirement="claude-agent-sdk==0.1.43")
    lines = [line async for line in installer.install(component)]

    assert lines == ["Collecting claude-agent-sdk==0.1.43", "Successfully installed"]
    assert captured_cmd[0] == sys.executable
    assert "-m" in captured_cmd and "pip" in captured_cmd
    assert "--target" in captured_cmd
    target_index = captured_cmd.index("--target")
    assert captured_cmd[target_index + 1] == str(pyenv_dir())
    assert captured_cmd[-1] == "claude-agent-sdk==0.1.43"


@pytest.mark.asyncio
async def test_install_raises_on_nonzero_exit(plugin_home, monkeypatch):
    async def fake_create_subprocess_exec(*cmd, **kwargs):
        return _FakeProcess([b"ERROR: could not find a version\n"], returncode=1)

    monkeypatch.setattr(
        "backend.integrations.plugins._installers.base.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    installer = PipTargetInstaller()
    component = InstallComponent(kind="pip", requirement="claude-agent-sdk==0.1.43")

    with pytest.raises(PluginInstallSubprocessError):
        _ = [line async for line in installer.install(component)]


def test_detect_reports_not_installed_when_dir_absent(plugin_home):
    installer = PipTargetInstaller()
    component = InstallComponent(kind="pip", requirement="claude-agent-sdk==0.1.43")
    state = installer.detect(component)
    assert state.installed is False
    assert state.version is None
    assert state.target_version == "0.1.43"
    assert state.update_available is False


def test_detect_reports_update_available_when_version_below_pin(plugin_home):
    from xyz_agent_context.agent_framework.plugin_paths import pyenv_dir

    _write_fake_dist(pyenv_dir(), "claude_agent_sdk", "claude_agent_sdk", "0.1.40")
    installer = PipTargetInstaller()
    component = InstallComponent(kind="pip", requirement="claude-agent-sdk==0.1.43")
    state = installer.detect(component)
    assert state.installed is True
    assert state.version == "0.1.40"
    assert state.target_version == "0.1.43"
    assert state.update_available is True


def test_detect_reports_no_update_when_version_matches_pin(plugin_home):
    from xyz_agent_context.agent_framework.plugin_paths import pyenv_dir

    _write_fake_dist(pyenv_dir(), "claude_agent_sdk", "claude_agent_sdk", "0.1.43")
    installer = PipTargetInstaller()
    component = InstallComponent(kind="pip", requirement="claude-agent-sdk==0.1.43")
    state = installer.detect(component)
    assert state.installed is True
    assert state.version == "0.1.43"
    assert state.update_available is False


@pytest.mark.asyncio
async def test_uninstall_removes_package_dir_and_dist_info(plugin_home):
    from xyz_agent_context.agent_framework.plugin_paths import pyenv_dir

    _write_fake_dist(pyenv_dir(), "claude_agent_sdk", "claude_agent_sdk", "0.1.43")
    installer = PipTargetInstaller()
    component = InstallComponent(kind="pip", requirement="claude-agent-sdk==0.1.43")

    assert (pyenv_dir() / "claude_agent_sdk").is_dir()
    await installer.uninstall(component)

    assert not (pyenv_dir() / "claude_agent_sdk").exists()
    assert list(pyenv_dir().glob("claude_agent_sdk-*.dist-info")) == []
