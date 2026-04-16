"""Tests for _lark_workspace — path traversal, directory creation, env override."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from xyz_agent_context.module.lark_module._lark_workspace import (
    get_workspace_path,
    ensure_workspace,
    get_home_env,
    cleanup_workspace,
)


class TestGetWorkspacePath:
    def test_normal_id(self):
        path = get_workspace_path("agent_123")
        assert path.name == "agent_123"
        assert "lark_workspaces" in str(path)

    def test_path_traversal_slash(self):
        path = get_workspace_path("../../etc/passwd")
        assert ".." not in path.name
        assert "/" not in path.name

    def test_path_traversal_dotdot(self):
        path = get_workspace_path("..secret")
        assert ".." not in path.name

    def test_custom_base_dir(self, tmp_path):
        with patch.dict(os.environ, {"LARK_WORKSPACE_BASE": str(tmp_path)}):
            path = get_workspace_path("test_agent")
            assert str(path).startswith(str(tmp_path))


class TestEnsureWorkspace:
    def test_creates_directory(self, tmp_path):
        with patch.dict(os.environ, {"LARK_WORKSPACE_BASE": str(tmp_path)}):
            workspace = ensure_workspace("test_agent")
            assert workspace.exists()
            assert workspace.is_dir()

    def test_idempotent(self, tmp_path):
        with patch.dict(os.environ, {"LARK_WORKSPACE_BASE": str(tmp_path)}):
            ws1 = ensure_workspace("test_agent")
            ws2 = ensure_workspace("test_agent")
            assert ws1 == ws2
            assert ws1.exists()

    def test_restrictive_permissions(self, tmp_path):
        with patch.dict(os.environ, {"LARK_WORKSPACE_BASE": str(tmp_path)}):
            workspace = ensure_workspace("test_agent")
            mode = workspace.stat().st_mode & 0o777
            assert mode == 0o700


class TestGetHomeEnv:
    def test_home_overridden(self, tmp_path):
        with patch.dict(os.environ, {"LARK_WORKSPACE_BASE": str(tmp_path)}):
            env = get_home_env("test_agent")
            assert env["HOME"] != os.environ.get("HOME", "")
            assert "test_agent" in env["HOME"]

    def test_inherits_parent_env(self, tmp_path):
        with patch.dict(os.environ, {
            "LARK_WORKSPACE_BASE": str(tmp_path),
            "MY_CUSTOM_VAR": "test_value",
        }):
            env = get_home_env("test_agent")
            assert env["MY_CUSTOM_VAR"] == "test_value"

    def test_creates_workspace(self, tmp_path):
        with patch.dict(os.environ, {"LARK_WORKSPACE_BASE": str(tmp_path)}):
            env = get_home_env("new_agent")
            assert Path(env["HOME"]).exists()


class TestCleanupWorkspace:
    def test_removes_directory(self, tmp_path):
        with patch.dict(os.environ, {"LARK_WORKSPACE_BASE": str(tmp_path)}):
            workspace = ensure_workspace("doomed_agent")
            (workspace / "test_file.txt").write_text("data")
            assert workspace.exists()

            cleanup_workspace("doomed_agent")
            assert not workspace.exists()

    def test_noop_if_not_exists(self, tmp_path):
        """Should not raise if workspace doesn't exist."""
        with patch.dict(os.environ, {"LARK_WORKSPACE_BASE": str(tmp_path)}):
            cleanup_workspace("nonexistent_agent")  # no exception
