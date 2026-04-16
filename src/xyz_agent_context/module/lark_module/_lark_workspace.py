"""
@file_name: _lark_workspace.py
@date: 2026-04-16
@description: Per-agent workspace manager for HOME-based lark-cli isolation.

Each agent gets its own workspace directory. When lark-cli runs with
HOME set to this directory, it reads/writes ~/.lark-cli/ config and
cache inside the workspace — naturally isolating per agent.
"""

from __future__ import annotations

import os
from pathlib import Path

from loguru import logger

# Default base directory for agent workspaces
_DEFAULT_BASE = Path.home() / ".narranexus" / "lark_workspaces"


def _get_base_dir() -> Path:
    """Return base directory for workspaces (configurable via env var)."""
    return Path(os.environ.get("LARK_WORKSPACE_BASE", str(_DEFAULT_BASE)))


def get_workspace_path(agent_id: str) -> Path:
    """Return the workspace path for an agent (does not create it)."""
    # Prevent path traversal
    safe_id = agent_id.replace("/", "_").replace("..", "_")
    return _get_base_dir() / safe_id


def ensure_workspace(agent_id: str) -> Path:
    """Create workspace directory if it doesn't exist. Returns the path."""
    workspace = get_workspace_path(agent_id)
    workspace.mkdir(parents=True, exist_ok=True)
    # Restrictive permissions (owner only)
    try:
        workspace.chmod(0o700)
    except OSError:
        pass  # Windows doesn't support chmod
    return workspace


def get_home_env(agent_id: str) -> dict[str, str]:
    """Return environment dict with HOME pointing to the workspace.

    Inherits the full parent env (needed for macOS Keychain access,
    Node.js paths, etc.) but overrides HOME for lark-cli config isolation.
    """
    workspace = ensure_workspace(agent_id)
    env = {**os.environ, "HOME": str(workspace)}
    return env


def cleanup_workspace(agent_id: str) -> None:
    """Remove workspace directory on unbind."""
    import shutil
    workspace = get_workspace_path(agent_id)
    if workspace.exists():
        shutil.rmtree(workspace, ignore_errors=True)
        logger.info(f"Cleaned up Lark workspace for {agent_id}")
