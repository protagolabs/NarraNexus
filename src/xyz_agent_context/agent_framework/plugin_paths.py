"""
@file_name: plugin_paths.py
@author: NarraNexus
@date: 2026-08-28
@description: Single source of truth for WHERE the optional framework plugins
              (Claude Code, Codex) install on the local/desktop build, and
              whether they are present.

Why this module exists
----------------------
On the local build (``bash run.sh`` and the desktop DMG) the two heavyweight
coding-agent frameworks are no longer bundled — the user installs them on
demand from Settings → Plugins. Both land in ONE user-writable tree,
``~/.narranexus/plugins/`` (never inside the read-only, notarized ``.app``):

    ~/.narranexus/plugins/
    ├── nodejs/                          # npm --prefix target (Claude CLI 2.1.220)
    │   └── node_modules/.bin/claude
    └── pyenv/                           # uv pip --target (the SDK wheels)
        ├── claude_agent_sdk/            #   Claude framework (brings its 2.1.56 CLI)
        └── openai_codex/                #   Codex framework

This module is PURE: path arithmetic plus filesystem/``find_spec`` probes. It
holds no version pins (those live with the installer) and imports nothing from
``backend`` (铁律 #21). It is imported by three consumers that must agree on
the layout: the lazy driver factories (``agent_framework/__init__``), the
Claude binary resolver (``adapters/claude/cli_binary``), and the backend
install/status routes.

Availability vs. import
-----------------------
``framework_installed`` answers "is this framework's code present" WITHOUT
importing it — a filesystem check of the plugin ``pyenv`` OR a
``find_spec`` in the ordinary environment (so a cloud image that pre-installs
the SDKs the normal way reports installed too). ``activate_pyenv`` is the local
seam that makes an installed plugin importable in-process without a restart: it
APPENDS the ``pyenv`` to ``sys.path`` so base packages still win for shared
dependencies and the ``pyenv`` only fills the gap of the plugin-only wheels.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

# Env override — lets tests (and any relocated install) point the whole tree
# elsewhere. Empty/unset falls back to the per-user default below.
ENV_PLUGIN_HOME = "NARRANEXUS_PLUGIN_HOME"

# framework name → the top-level python package whose presence proves the
# plugin is installed. ``nexus_power`` is built-in and needs no probe.
_FRAMEWORK_PACKAGE: dict[str, str] = {
    "claude_code": "claude_agent_sdk",
    "codex_cli": "openai_codex",
}

# The frameworks whose availability is gated on an optional plugin. Only these
# are fail-closed in get_agent_loop_driver — a built-in (nexus_power) or any
# custom-registered driver is available by virtue of being registered.
PLUGIN_FRAMEWORKS: frozenset[str] = frozenset(_FRAMEWORK_PACKAGE)


def plugin_home() -> Path:
    """Root of the user-writable plugin tree (env-overridable)."""
    override = os.environ.get(ENV_PLUGIN_HOME, "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".narranexus" / "plugins"


def node_prefix() -> Path:
    """``npm install --prefix`` target for the Claude CLI."""
    return plugin_home() / "nodejs"


def pyenv_dir() -> Path:
    """``uv pip install --target`` directory for the framework SDK wheels."""
    return plugin_home() / "pyenv"


def claude_cli_path() -> Path:
    """Absolute path of the managed ``claude`` binary (may not exist yet)."""
    return node_prefix() / "node_modules" / ".bin" / "claude"


def _present_in_pyenv(package: str) -> bool:
    """True if ``package`` is installed under the plugin ``pyenv`` (no import)."""
    return (pyenv_dir() / package).is_dir()


def _present_in_base(package: str) -> bool:
    """True if ``package`` is importable from the ordinary environment.

    Covers the cloud / non-plugin install where the SDK sits in the normal
    site-packages. ``find_spec`` locates without executing the module, so it is
    safe to call from the backend process (it does not pull the ~186 MB SDK
    into memory). Isolated as a function so tests can force the miss branch.
    """
    try:
        return importlib.util.find_spec(package) is not None
    except (ImportError, ValueError):
        # A half-installed parent package can raise here; treat as absent.
        return False


def framework_installed(name: str) -> bool:
    """Whether the coding-agent framework ``name`` is available to run.

    ``nexus_power`` is built-in (always True). ``claude_code`` / ``codex_cli``
    are present when their package is in the plugin ``pyenv`` OR in the base
    environment. Any other name is unknown → False.
    """
    if name == "nexus_power":
        return True
    package = _FRAMEWORK_PACKAGE.get(name)
    if package is None:
        return False
    return _present_in_pyenv(package) or _present_in_base(package)


def activate_pyenv() -> None:
    """Make installed plugin packages importable in THIS process.

    APPENDS the plugin ``pyenv`` to ``sys.path`` (base wins shared deps; the
    pyenv only supplies the plugin-only wheels). Idempotent, and a no-op when
    nothing is installed. This is the local/desktop seam: the in-process driver
    factory calls it right before importing the framework SDK, so a just-
    installed plugin works without restarting the app.
    """
    directory = pyenv_dir()
    if not directory.is_dir():
        return
    entry = str(directory)
    if entry not in sys.path:
        sys.path.append(entry)
