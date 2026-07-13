"""
@file_name: test_officecli_client.py
@author: rujing.yan
@date: 2026-07-13
@description: Unit tests for OfficeCLIClient (preview rendering + path rules) and
the office_cli command-security gate. The officecli binary is mocked, so these
run without OfficeCLI installed.
"""
from __future__ import annotations

import os

import pytest

from xyz_agent_context.module.office_module._office_impl._office_command_security import (
    sanitize_command,
    validate_command,
)
from xyz_agent_context.module.office_module._office_impl.officecli_client import (
    OFFICE_EXT_TO_KIND,
    OfficeCLIClient,
    preview_name_for,
)
from xyz_agent_context.schema.artifact_schema import ArtifactKind
from xyz_agent_context.utils.workspace_paths import agent_workspace_relpath


# ── command security ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("cmd", ["watch deck.pptx", "install", "config init", "mcp claude"])
def test_blocked_subcommands(cmd):
    allowed, reason = validate_command(cmd)
    assert allowed is False
    assert reason


@pytest.mark.parametrize(
    "cmd",
    [
        "create office/d/x.pptx",
        "view office/r/report.docx outline",
        'add office/d/x.pptx / --type slide --prop title="Q4 Report"',
        "docx view report.docx text",
    ],
)
def test_allowed_commands(cmd):
    allowed, _ = validate_command(cmd)
    assert allowed is True


def test_sanitize_splits_and_preserves_quoted_args():
    args = sanitize_command('add x.pptx / --prop title="Q4 Report"')
    assert args == ["add", "x.pptx", "/", "--prop", "title=Q4 Report"]


def test_sanitize_rejects_blocked():
    with pytest.raises(ValueError):
        sanitize_command("watch x.pptx")


# ── kind mapping stays in sync with the schema whitelist ─────────────────────


def test_office_kinds_are_valid_artifact_kinds():
    valid = set(ArtifactKind.__args__)  # type: ignore[attr-defined]
    for kind in OFFICE_EXT_TO_KIND.values():
        assert kind in valid


def test_preview_name_for():
    assert preview_name_for("slides.pptx") == "slides.preview.html"
    assert preview_name_for("a/b/report.docx") == "report.preview.html"


# ── render_preview path rules (officecli mocked) ─────────────────────────────


@pytest.fixture
def ws(monkeypatch, tmp_path):
    """Point base_working_path at a temp dir and return the agent workspace."""
    base = tmp_path / "workspaces"
    base.mkdir()
    from xyz_agent_context.settings import settings as sa_settings
    monkeypatch.setattr(sa_settings, "base_working_path", str(base), raising=False)
    # attachment_storage.get_workspace_path derives from base_working_path too;
    # patch it to the same layout so client + registration agree.
    workspace = base / agent_workspace_relpath("agent_x", "user_y")
    workspace.mkdir(parents=True)

    import xyz_agent_context.utils.attachment_storage as att
    monkeypatch.setattr(att, "get_workspace_path", lambda a, u: base / agent_workspace_relpath(a, u))
    return workspace


@pytest.mark.asyncio
async def test_render_rejects_root_level_file(ws, monkeypatch):
    client = OfficeCLIClient()
    doc = ws / "report.docx"
    doc.write_bytes(b"fake")
    res = await client.render_preview("agent_x", "user_y", "report.docx")
    assert res["success"] is False
    assert "subdirectory" in res["error"]


@pytest.mark.asyncio
async def test_render_rejects_unknown_extension(ws):
    client = OfficeCLIClient()
    (ws / "d").mkdir()
    (ws / "d" / "notes.txt").write_text("x")
    res = await client.render_preview("agent_x", "user_y", "d/notes.txt")
    assert res["success"] is False
    assert "unsupported" in res["error"]


@pytest.mark.asyncio
async def test_render_happy_path_writes_preview_and_returns_kind(ws, monkeypatch):
    client = OfficeCLIClient()
    (ws / "deck").mkdir()
    (ws / "deck" / "slides.pptx").write_bytes(b"PK fake")

    async def fake_exec(cmd, *, cwd, timeout):
        # emulate `officecli view <file> html -o <out>` writing the preview
        out = cmd[cmd.index("-o") + 1]
        with open(out, "w", encoding="utf-8") as f:
            f.write("<html>preview</html>")
        return {"success": True, "data": {}}

    monkeypatch.setattr(client, "_exec", fake_exec)
    res = await client.render_preview("agent_x", "user_y", "deck/slides.pptx")
    assert res["success"] is True
    assert res["preview_rel"] == "deck/slides.preview.html"
    assert res["office_rel"] == "deck/slides.pptx"
    assert res["kind"] == OFFICE_EXT_TO_KIND[".pptx"]
    assert os.path.isfile(res["preview_abs"])
