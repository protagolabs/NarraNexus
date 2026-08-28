"""
@file_name: pip_target.py
@author: NarraNexus
@date: 2026-08-28
@description: PluginInstaller strategy that installs a pip wheel into the
              user-writable plugin pyenv via ``sys.executable -m pip install
              --target <pyenv_dir>``.

Uses ``sys.executable -m pip`` rather than a bare ``pip`` / ``uv`` binary on
PATH so the wheel's ABI always matches the interpreter that will later
``sys.path.append`` and import it (``agent_framework.plugin_paths.
activate_pyenv``) — a wheel built for a different interpreter would import
but crash on any C-extension boundary.
"""
from __future__ import annotations

import importlib.metadata
import re
import shutil
import sys
from typing import AsyncIterator

from xyz_agent_context.agent_framework.plugin_paths import pyenv_dir

from ..spec import InstallComponent
from .base import InstalledState, PluginInstaller, stream_subprocess

# Splits "claude-agent-sdk==0.1.43" into the distribution name and the pinned
# version. Installers only ever receive exact-pin requirements (registry.py
# is the single place that writes them), so "==" is the only operator to
# support.
_REQUIREMENT_RE = re.compile(r"^([A-Za-z0-9_.\-]+)==([A-Za-z0-9_.\-]+)$")


def _parse_requirement(requirement: str) -> tuple[str, str]:
    match = _REQUIREMENT_RE.match(requirement.strip())
    if not match:
        raise ValueError(f"unsupported pip requirement (expected name==version): {requirement!r}")
    return match.group(1), match.group(2)


def _dist_dir_name(distribution_name: str) -> str:
    """Normalize a distribution name the way wheel/dist-info directories do
    (PEP 503-ish): non-alphanumeric runs become a single underscore."""
    return re.sub(r"[^A-Za-z0-9]+", "_", distribution_name).lower()


class PipTargetInstaller(PluginInstaller):
    async def install(self, component: InstallComponent) -> AsyncIterator[str]:
        cmd = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--target",
            str(pyenv_dir()),
            component.requirement,
        ]
        async for line in stream_subprocess(cmd):
            yield line

    def detect(self, component: InstallComponent) -> InstalledState:
        distribution_name, target_version = _parse_requirement(component.requirement)
        dir_name = _dist_dir_name(distribution_name)
        package_path = pyenv_dir() / dir_name
        if not package_path.is_dir():
            return InstalledState(
                installed=False,
                version=None,
                target_version=target_version,
                update_available=False,
            )
        version = self._read_version(dir_name)
        update_available = version is not None and version != target_version
        return InstalledState(
            installed=True,
            version=version,
            target_version=target_version,
            update_available=update_available,
        )

    def _read_version(self, dir_name: str) -> str | None:
        matches = sorted(pyenv_dir().glob(f"{dir_name}-*.dist-info"))
        if not matches:
            return None
        try:
            return importlib.metadata.PathDistribution(matches[-1]).version
        except Exception:  # noqa: BLE001 — a malformed dist-info reads as "unknown version"
            return None

    async def uninstall(self, component: InstallComponent) -> None:
        distribution_name, _ = _parse_requirement(component.requirement)
        dir_name = _dist_dir_name(distribution_name)
        package_path = pyenv_dir() / dir_name
        if package_path.is_dir():
            shutil.rmtree(package_path, ignore_errors=True)
        for dist_info in pyenv_dir().glob(f"{dir_name}-*.dist-info"):
            shutil.rmtree(dist_info, ignore_errors=True)
