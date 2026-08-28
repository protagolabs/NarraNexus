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
    └── pyenv/                           # pip --target root, ONE subdir per plugin
        ├── claude_code/                 #   claude_agent_sdk + its deps (its 2.1.56 CLI)
        └── codex_cli/                   #   openai_codex + openai_codex_cli_bin + deps

The pip tree is split ONE subdir per plugin (keyed by plugin id / framework
name) on purpose: uninstall is then a single ``rmtree`` of the plugin's subdir
that takes the WHOLE dependency closure with it (a flat shared ``pyenv`` would
strand ~90 MB of ``openai_codex_cli_bin`` + shared deps, and two plugins would
fight over one copy of a shared dependency's version). npm stays a single
shared ``nodejs`` prefix — only Claude uses it, and npm uninstalls by package.

This module is PURE: path arithmetic plus filesystem/``find_spec`` probes. It
holds no version pins (those live with the installer) and imports nothing from
``backend`` (铁律 #21). It is imported by consumers that must agree on the
layout: the lazy driver factories (``agent_framework/__init__``), the Claude
binary resolver (``adapters/claude/cli_binary``), the helper-LLM / OAuth paths
(``llm/cli_helper``, ``providers/driver/drivers/claude_oauth``), and the
backend install/status routes / installers.

Availability vs. import
-----------------------
``framework_installed`` answers "is this framework's code present" WITHOUT
importing it — a filesystem check of the plugin's ``pyenv`` subdir OR a
``find_spec`` in the ordinary environment (so a cloud image that pre-installs
the SDKs the normal way reports installed too). ``activate_pyenv`` is the local
seam that makes an installed plugin importable in-process without a restart: it
APPENDS every existing plugin subdir to ``sys.path`` so base packages still win
for shared dependencies and the subdirs only fill the gap of the plugin wheels.
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
    """Root of the pip ``--target`` tree (holds one subdir per plugin)."""
    return plugin_home() / "pyenv"


def plugin_pyenv(plugin_id: str) -> Path:
    """``pip install --target`` directory for ONE plugin's python packages.

    One subdir per plugin so uninstall is a clean ``rmtree`` of the whole
    dependency closure. ``plugin_id`` equals the framework name (claude_code /
    codex_cli)."""
    return pyenv_dir() / plugin_id


def claude_cli_path() -> Path:
    """Absolute path of the managed ``claude`` binary (may not exist yet)."""
    return node_prefix() / "node_modules" / ".bin" / "claude"


def _present_in_pyenv(name: str, package: str) -> bool:
    """True if ``package`` is installed under plugin ``name``'s pyenv subdir."""
    return (plugin_pyenv(name) / package).is_dir()


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
    return _present_in_pyenv(name, package) or _present_in_base(package)


def activate_pyenv() -> None:
    """Make installed plugin packages importable in THIS process.

    APPENDS every existing per-plugin ``pyenv`` subdir to ``sys.path`` (base
    wins shared deps; the subdirs only supply the plugin wheels). Idempotent,
    and a no-op when nothing is installed. This is the local/desktop seam:
    called right before importing a framework SDK (the driver factories,
    ``sdk._ensure_sdk_imported``, and the helper-LLM / OAuth paths), so a
    just-installed plugin works without restarting the app.
    """
    root = pyenv_dir()
    if not root.is_dir():
        return
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        entry = str(child)
        if entry not in sys.path:
            sys.path.append(entry)
