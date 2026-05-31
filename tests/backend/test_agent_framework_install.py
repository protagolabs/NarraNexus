"""
@file_name: test_agent_framework_install.py
@date: 2026-05-30
@description: Tests for ``_ensure_codex_installed`` in
``backend/routes/providers.py``. The helper auto-installs
``@openai/codex`` via ``npm install -g`` when the user opts into the
codex_cli framework from the Settings page.

We don't actually run ``npm`` — every test patches
``asyncio.create_subprocess_exec`` and ``shutil.which`` so the
control flow can be exercised deterministically in CI.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.routes.providers import _ensure_codex_installed


class _FakeProc:
    """Minimal asyncio subprocess stand-in."""

    def __init__(self, returncode: int = 0, stderr: bytes = b""):
        self.returncode = returncode
        self._stderr = stderr
        self.killed = False

    async def communicate(self):
        return (b"", self._stderr)

    def kill(self):
        self.killed = True
        # Simulate the OS kernel reaping the process
        self.returncode = -9

    async def wait(self):
        return self.returncode


# ---------------- happy / already-installed -----------------------


@pytest.mark.asyncio
async def test_returns_already_installed_when_codex_on_path():
    with patch("shutil.which", side_effect=lambda x: "/usr/local/bin/codex" if x == "codex" else None):
        r = await _ensure_codex_installed()
    assert r == {"installed": True, "action": "already_installed", "reason": ""}


@pytest.mark.asyncio
async def test_auto_installs_when_codex_missing_and_local_mode():
    """In local mode, missing codex → spawn npm install → success."""
    # First .which("codex") → None (not installed). After "npm install"
    # finishes we check .which("codex") again → returns a path (success).
    which_calls = {"codex": 0}
    def which(name):
        if name == "codex":
            which_calls["codex"] += 1
            # First call: missing. Second call (after npm install): present.
            return None if which_calls["codex"] == 1 else "/usr/local/bin/codex"
        if name == "npm":
            return "/usr/local/bin/npm"
        return None

    fake_exec = AsyncMock(return_value=_FakeProc(returncode=0))
    with patch("shutil.which", side_effect=which), \
         patch("xyz_agent_context.utils.deployment_mode.get_deployment_mode", return_value="local"), \
         patch("asyncio.create_subprocess_exec", fake_exec):
        r = await _ensure_codex_installed()
    assert r["installed"] is True
    assert r["action"] == "auto_installed"
    # Sanity: we actually invoked npm
    fake_exec.assert_called_once()
    args = fake_exec.call_args[0]
    assert args[0] == "npm"
    assert "install" in args
    assert "-g" in args
    assert "@openai/codex" in args


# ---------------- cloud mode block --------------------------------


@pytest.mark.asyncio
async def test_blocks_in_cloud_mode():
    """Cloud mode: refuse global install on shared host."""
    with patch("shutil.which", return_value=None), \
         patch("xyz_agent_context.utils.deployment_mode.get_deployment_mode", return_value="cloud"):
        r = await _ensure_codex_installed()
    assert r["installed"] is False
    assert r["action"] == "blocked"
    assert "cloud" in r["reason"].lower()


# ---------------- failure paths -----------------------------------


@pytest.mark.asyncio
async def test_fails_when_npm_not_installed():
    """No npm → cannot auto-install."""
    def which(name):
        return None  # neither codex nor npm
    with patch("shutil.which", side_effect=which), \
         patch("xyz_agent_context.utils.deployment_mode.get_deployment_mode", return_value="local"):
        r = await _ensure_codex_installed()
    assert r["installed"] is False
    assert r["action"] == "install_failed"
    assert "npm" in r["reason"].lower()


@pytest.mark.asyncio
async def test_fails_when_npm_install_returns_non_zero():
    """npm exits with error → surface it."""
    def which(name):
        if name == "npm":
            return "/usr/local/bin/npm"
        return None  # codex missing both before and after (install failed)
    fake_exec = AsyncMock(return_value=_FakeProc(
        returncode=1, stderr=b"npm ERR! EACCES permission denied",
    ))
    with patch("shutil.which", side_effect=which), \
         patch("xyz_agent_context.utils.deployment_mode.get_deployment_mode", return_value="local"), \
         patch("asyncio.create_subprocess_exec", fake_exec):
        r = await _ensure_codex_installed()
    assert r["installed"] is False
    assert r["action"] == "install_failed"
    assert "rc=1" in r["reason"]
    assert "EACCES" in r["reason"]


@pytest.mark.asyncio
async def test_fails_when_npm_succeeds_but_binary_not_on_path():
    """npm exit 0 but codex still missing → PATH issue."""
    def which(name):
        if name == "npm":
            return "/usr/local/bin/npm"
        return None  # codex absent before AND after
    fake_exec = AsyncMock(return_value=_FakeProc(returncode=0))
    with patch("shutil.which", side_effect=which), \
         patch("xyz_agent_context.utils.deployment_mode.get_deployment_mode", return_value="local"), \
         patch("asyncio.create_subprocess_exec", fake_exec):
        r = await _ensure_codex_installed()
    assert r["installed"] is False
    assert r["action"] == "install_failed"
    assert "PATH" in r["reason"]


@pytest.mark.asyncio
async def test_fails_on_timeout():
    """npm install hangs > timeout → kill + report."""
    import asyncio as _asyncio

    class _SlowProc(_FakeProc):
        async def communicate(self):
            await _asyncio.sleep(3600)  # would never finish
            return (b"", b"")

    fake_exec = AsyncMock(return_value=_SlowProc())
    def which(name):
        if name == "npm":
            return "/usr/local/bin/npm"
        return None
    with patch("shutil.which", side_effect=which), \
         patch("xyz_agent_context.utils.deployment_mode.get_deployment_mode", return_value="local"), \
         patch("asyncio.create_subprocess_exec", fake_exec), \
         patch("backend.routes.providers._CODEX_NPM_INSTALL_TIMEOUT", 0.05):
        r = await _ensure_codex_installed()
    assert r["installed"] is False
    assert r["action"] == "install_failed"
    assert "timed out" in r["reason"].lower()
