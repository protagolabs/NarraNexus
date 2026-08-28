"""
@file_name: npm_prefix.py
@author: NarraNexus
@date: 2026-08-28
@description: PluginInstaller strategy that installs the Claude CLI into
              the user-writable plugin node tree via ``npm install --prefix
              <node_prefix>``. Assumes ``node``/``npm`` are on PATH (bundled
              with the desktop app / expected on the host for ``run.sh``).
"""
from __future__ import annotations

import re
import shutil
import subprocess
from typing import AsyncIterator

from xyz_agent_context.agent_framework.plugin_paths import claude_cli_path, node_prefix

from ..spec import InstallComponent
from .base import InstalledState, PluginInstaller, stream_subprocess

# "@anthropic-ai/claude-code@2.1.220" -> "2.1.220". The version is always the
# trailing "@<digit...>" segment; the package name itself may contain "@"
# (scoped packages), so anchor on a digit right after the last "@".
_NPM_REQUIREMENT_VERSION_RE = re.compile(r"@(\d[\w.\-]*)$")

# `claude --version` prints e.g. "2.1.220 (Claude Code)".
_CLI_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")

_VERSION_PROBE_TIMEOUT_S = 20.0


def _pinned_version(requirement: str) -> str:
    match = _NPM_REQUIREMENT_VERSION_RE.search(requirement.strip())
    if not match:
        raise ValueError(f"unsupported npm requirement (expected pkg@version): {requirement!r}")
    return match.group(1)


class NpmPrefixInstaller(PluginInstaller):
    async def install(self, component: InstallComponent) -> AsyncIterator[str]:
        cmd = ["npm", "install", "--prefix", str(node_prefix()), component.requirement]
        async for line in stream_subprocess(cmd):
            yield line

    def detect(self, component: InstallComponent) -> InstalledState:
        target_version = _pinned_version(component.requirement)
        cli_path = claude_cli_path()
        if not cli_path.exists():
            return InstalledState(
                installed=False,
                version=None,
                target_version=target_version,
                update_available=False,
            )
        version = self._probe_version(cli_path)
        update_available = version is not None and version != target_version
        return InstalledState(
            installed=version is not None,
            version=version,
            target_version=target_version,
            update_available=update_available,
        )

    def _probe_version(self, cli_path) -> str | None:
        try:
            result = subprocess.run(
                [str(cli_path), "--version"],
                capture_output=True,
                text=True,
                timeout=_VERSION_PROBE_TIMEOUT_S,
            )
        except Exception:  # noqa: BLE001 — an unreadable version reads as "unknown"
            return None
        match = _CLI_VERSION_RE.search((result.stdout or "") + (result.stderr or ""))
        return match.group(1) if match else None

    async def uninstall(self, component: InstallComponent) -> None:
        target_dir = node_prefix()
        if target_dir.is_dir():
            shutil.rmtree(target_dir, ignore_errors=True)
