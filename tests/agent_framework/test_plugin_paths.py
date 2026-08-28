"""
@file_name: test_plugin_paths.py
@author: NarraNexus
@date: 2026-08-28
@description: Contract tests for plugin_paths — the single source of truth for
              WHERE optional framework plugins install and whether they exist.

Regression guard: delete the target logic and one of these must go red.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from xyz_agent_context.agent_framework import plugin_paths as pp


@pytest.fixture()
def isolated_home(tmp_path, monkeypatch):
    """Point the plugin home at an empty temp dir via the env override."""
    home = tmp_path / "plugins"
    monkeypatch.setenv(pp.ENV_PLUGIN_HOME, str(home))
    return home


def test_env_override_wins(isolated_home):
    assert pp.plugin_home() == isolated_home


def test_default_home_when_env_unset(monkeypatch):
    monkeypatch.delenv(pp.ENV_PLUGIN_HOME, raising=False)
    assert pp.plugin_home() == Path.home() / ".narranexus" / "plugins"


def test_path_composition(isolated_home):
    assert pp.node_prefix() == isolated_home / "nodejs"
    assert pp.pyenv_dir() == isolated_home / "pyenv"
    assert pp.claude_cli_path() == (
        isolated_home / "nodejs" / "node_modules" / ".bin" / "claude"
    )


def test_nexus_power_always_installed(isolated_home):
    # No filesystem, no import — the built-in framework is unconditionally on.
    assert pp.framework_installed("nexus_power") is True


def test_unknown_framework_never_installed(isolated_home):
    assert pp.framework_installed("does_not_exist") is False


def test_claude_absent_when_neither_pyenv_nor_base(isolated_home, monkeypatch):
    # Force the base-env probe to miss so only the pyenv filesystem matters.
    monkeypatch.setattr(pp, "_present_in_base", lambda pkg: False)
    assert pp.framework_installed("claude_code") is False
    assert pp.framework_installed("codex_cli") is False


def test_claude_present_via_pyenv(isolated_home, monkeypatch):
    monkeypatch.setattr(pp, "_present_in_base", lambda pkg: False)
    (isolated_home / "pyenv" / "claude_agent_sdk").mkdir(parents=True)
    assert pp.framework_installed("claude_code") is True
    assert pp.framework_installed("codex_cli") is False  # only claude's dir exists


def test_codex_present_via_pyenv(isolated_home, monkeypatch):
    monkeypatch.setattr(pp, "_present_in_base", lambda pkg: False)
    (isolated_home / "pyenv" / "openai_codex").mkdir(parents=True)
    assert pp.framework_installed("codex_cli") is True


def test_present_via_base_env(isolated_home, monkeypatch):
    # Cloud / normal install: package sits in the ordinary site-packages,
    # not the plugin pyenv. Availability must still report True.
    monkeypatch.setattr(pp, "_present_in_base", lambda pkg: pkg == "claude_agent_sdk")
    assert pp.framework_installed("claude_code") is True
    assert pp.framework_installed("codex_cli") is False


def test_activate_pyenv_appends_when_present(isolated_home):
    (isolated_home / "pyenv").mkdir(parents=True)
    target = str(isolated_home / "pyenv")
    try:
        pp.activate_pyenv()
        assert target in sys.path
        # Appended (base wins shared deps), not prepended.
        assert sys.path.index(target) == len(sys.path) - 1
        # Idempotent.
        pp.activate_pyenv()
        assert sys.path.count(target) == 1
    finally:
        while target in sys.path:
            sys.path.remove(target)


def test_activate_pyenv_noop_when_absent(isolated_home):
    target = str(isolated_home / "pyenv")
    pp.activate_pyenv()
    assert target not in sys.path
