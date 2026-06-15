"""
Tests for the External-API workspace isolation introduced 2026-06-15.

Background — before this fix, every visitor that hit `/v1/external/*`
got their own empty `{base}/{aid}_{user}/` working directory. The
owner's curated skills + instructions + data lived under a different
`{aid}_{owner}/` and were inaccessible to the visitor — the agent ran
"naked" and performed badly. The fix:

  - When `ctx.policy is not None` (the External-runtime marker) and the
    visitor's working dir does not yet exist, we bootstrap it by
    symlinking each top-level entry of `{aid}_{owner}/` into the
    visitor dir, then create `uploads/` and `outputs/` as real
    writeable directories.

  - Main runtime path (no policy) keeps the prior behaviour — empty
    `os.makedirs` only.

  - SkillModule renders WORKSPACE_RULES_EXTERNAL in the prompt when
    `policy.memory_scope == "user"` so the LLM is told the read-only
    contract for owner-curated paths.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


# ─── _setup_agent_workspace — pure-function tests ─────────────────────────────


class TestSetupAgentWorkspace:
    """The workspace bootstrap helper is filesystem-only and has no DB
    or LLM dependencies, so we can test it directly on a tmp dir."""

    def test_existing_visitor_dir_is_left_untouched(self, tmp_path):
        from xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop import (
            _setup_agent_workspace,
        )
        visitor = tmp_path / "agentX_visitor1"
        visitor.mkdir()
        (visitor / "existing.txt").write_text("pre-existing")
        _setup_agent_workspace(
            visitor_path=str(visitor),
            agent_id="agentX",
            user_id="visitor1",
            base_working_path=str(tmp_path),
            owner_user_id="ownerY",
            is_external=True,
        )
        # Helper saw the dir already exists and returned immediately.
        assert (visitor / "existing.txt").exists()
        assert not (visitor / "uploads").exists()

    def test_main_runtime_creates_empty_dir(self, tmp_path):
        from xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop import (
            _setup_agent_workspace,
        )
        visitor = tmp_path / "agentX_owner"
        _setup_agent_workspace(
            visitor_path=str(visitor),
            agent_id="agentX",
            user_id="owner",
            base_working_path=str(tmp_path),
            owner_user_id="owner",
            is_external=False,
        )
        assert visitor.is_dir()
        # Main runtime path: no symlinks, no uploads/, no outputs/.
        assert list(visitor.iterdir()) == []

    def test_external_no_owner_id_creates_write_dirs_only(self, tmp_path):
        from xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop import (
            _setup_agent_workspace,
        )
        visitor = tmp_path / "agentX_visitor1"
        _setup_agent_workspace(
            visitor_path=str(visitor),
            agent_id="agentX",
            user_id="visitor1",
            base_working_path=str(tmp_path),
            owner_user_id=None,
            is_external=True,
        )
        assert visitor.is_dir()
        # Write dirs created defensively so skill writes don't blow up.
        assert (visitor / "uploads").is_dir()
        assert (visitor / "outputs").is_dir()

    def test_external_owner_workspace_missing_creates_write_dirs_only(self, tmp_path):
        from xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop import (
            _setup_agent_workspace,
        )
        visitor = tmp_path / "agentX_visitor1"
        # Owner_user_id given but owner workspace does NOT exist on disk.
        _setup_agent_workspace(
            visitor_path=str(visitor),
            agent_id="agentX",
            user_id="visitor1",
            base_working_path=str(tmp_path),
            owner_user_id="ownerY",  # No agentX_ownerY/ here
            is_external=True,
        )
        assert (visitor / "uploads").is_dir()
        assert (visitor / "outputs").is_dir()
        # No symlinks — owner workspace not found.
        owner_links = [
            p for p in visitor.iterdir() if p.is_symlink()
        ]
        assert owner_links == []

    def test_external_full_bootstrap_mirrors_owner_top_level(self, tmp_path):
        from xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop import (
            _setup_agent_workspace,
        )
        # Owner workspace with skills/, instructions.md, data/, secret.json
        owner = tmp_path / "agentX_ownerY"
        owner.mkdir()
        (owner / "skills").mkdir()
        (owner / "skills" / "photo_resize").mkdir()
        (owner / "skills" / "photo_resize" / "SKILL.md").write_text(
            "# Photo resize\n"
        )
        (owner / "instructions.md").write_text("Owner's instructions.")
        (owner / "data").mkdir()
        (owner / "data" / "ref.csv").write_text("id,name\n")
        (owner / "secret.json").write_text("{}")

        visitor = tmp_path / "agentX_extABC"
        _setup_agent_workspace(
            visitor_path=str(visitor),
            agent_id="agentX",
            user_id="extABC",
            base_working_path=str(tmp_path),
            owner_user_id="ownerY",
            is_external=True,
        )
        assert visitor.is_dir()

        # Top-level entries copied as real files / dirs (NOT symlinks) so
        # that `Glob` discovery sees them — the Claude Agent SDK's Glob
        # tool defaults to followSymbolicLinks=false and would otherwise
        # report "No files found".
        for name in ("skills", "instructions.md", "data", "secret.json"):
            entry = visitor / name
            assert entry.exists(), f"{name} should exist"
            assert not entry.is_symlink(), f"{name} should be a real entry, not a symlink"

        # Visitor's own write dirs are REAL directories.
        assert (visitor / "uploads").is_dir()
        assert (visitor / "outputs").is_dir()

        # Content is intact across the copy.
        assert (visitor / "instructions.md").read_text() == "Owner's instructions."
        assert (visitor / "skills" / "photo_resize" / "SKILL.md").read_text() == (
            "# Photo resize\n"
        )
        assert (visitor / "data" / "ref.csv").read_text() == "id,name\n"

    def test_owner_uploads_or_outputs_collision_is_skipped(self, tmp_path):
        from xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop import (
            _setup_agent_workspace,
        )
        # Defensive: if owner happened to have uploads/ or outputs/, the
        # visitor's own dirs MUST win. Owner's same-named content is
        # NOT mirrored.
        owner = tmp_path / "agentX_ownerY"
        owner.mkdir()
        (owner / "uploads").mkdir()
        (owner / "uploads" / "owner_file.txt").write_text("OWNER OWNS THIS")
        (owner / "skills").mkdir()

        visitor = tmp_path / "agentX_extABC"
        _setup_agent_workspace(
            visitor_path=str(visitor),
            agent_id="agentX",
            user_id="extABC",
            base_working_path=str(tmp_path),
            owner_user_id="ownerY",
            is_external=True,
        )
        # uploads/ must be EMPTY (visitor's own dir, no owner content).
        uploads = visitor / "uploads"
        assert uploads.is_dir()
        assert list(uploads.iterdir()) == []
        # skills/ on the other hand WAS mirrored.
        assert (visitor / "skills").is_dir()

    def test_visitor_writes_to_uploads_do_not_touch_owner(self, tmp_path):
        from xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop import (
            _setup_agent_workspace,
        )
        owner = tmp_path / "agentX_ownerY"
        owner.mkdir()
        (owner / "skills").mkdir()
        (owner / "skills" / "test_skill").mkdir()
        (owner / "skills" / "test_skill" / "SKILL.md").write_text("# A skill")

        visitor = tmp_path / "agentX_extABC"
        _setup_agent_workspace(
            visitor_path=str(visitor),
            agent_id="agentX",
            user_id="extABC",
            base_working_path=str(tmp_path),
            owner_user_id="ownerY",
            is_external=True,
        )
        # Simulate the agent uploading a file.
        (visitor / "uploads" / "photo.jpg").write_bytes(b"\x89PNG visitor data")
        # Owner workspace must be untouched.
        assert not (owner / "uploads").exists()

        # Simulate a buggy/misbehaving agent writing into its copy of
        # skills/ — this MUST NOT touch the owner side, because the
        # visitor's copy is fully isolated.
        (visitor / "skills" / "test_skill" / "SKILL.md").write_text("VANDALIZED")
        assert (owner / "skills" / "test_skill" / "SKILL.md").read_text() == "# A skill"


# ─── SkillModule workspace-rules picker ──────────────────────────────────────


class TestWorkspaceRulesPicker:
    def test_external_policy_picks_external_rules(self):
        from xyz_agent_context.module.skill_module.skill_module import (
            WORKSPACE_RULES_EXTERNAL,
            _resolve_workspace_rules,
        )
        class P:
            memory_scope = "user"
        class C:
            deployment_mode = "cloud"  # Even with cloud mode, external wins.
        assert _resolve_workspace_rules(C(), policy=P()) is WORKSPACE_RULES_EXTERNAL

    def test_external_policy_beats_local_mode(self):
        from xyz_agent_context.module.skill_module.skill_module import (
            WORKSPACE_RULES_EXTERNAL,
            _resolve_workspace_rules,
        )
        class P:
            memory_scope = "user"
        class C:
            deployment_mode = "local"
        assert _resolve_workspace_rules(C(), policy=P()) is WORKSPACE_RULES_EXTERNAL

    def test_no_policy_falls_back_to_cloud_or_local(self):
        from xyz_agent_context.module.skill_module.skill_module import (
            WORKSPACE_RULES_CLOUD,
            WORKSPACE_RULES_LOCAL,
            _resolve_workspace_rules,
        )
        class C_local:
            deployment_mode = "local"
        class C_cloud:
            deployment_mode = "cloud"
        assert _resolve_workspace_rules(C_local(), policy=None) is WORKSPACE_RULES_LOCAL
        assert _resolve_workspace_rules(C_cloud(), policy=None) is WORKSPACE_RULES_CLOUD

    def test_policy_with_agent_scope_does_not_force_external(self):
        # An external runtime variant that doesn't restrict memory to per-user
        # scope should NOT get the external workspace rules. The signal we
        # branch on is specifically `policy.memory_scope == "user"`.
        from xyz_agent_context.module.skill_module.skill_module import (
            WORKSPACE_RULES_CLOUD,
            WORKSPACE_RULES_EXTERNAL,
            _resolve_workspace_rules,
        )
        class P:
            memory_scope = "agent"
        class C:
            deployment_mode = "cloud"
        result = _resolve_workspace_rules(C(), policy=P())
        assert result is not WORKSPACE_RULES_EXTERNAL
        assert result is WORKSPACE_RULES_CLOUD
