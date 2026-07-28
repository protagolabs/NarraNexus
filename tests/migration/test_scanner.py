"""
Agent Migration Scanner — detection + extraction against the REAL per-framework
source layouts (verified vs Hermes agent_import.py / openclaw_to_hermes.py).

Hermetic: Path.home() is monkeypatched to tmp so no real ~/.claude leaks in.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from xyz_agent_context.migration import detector, scanner
from xyz_agent_context.migration.extractors import _memory_from_md, _mcp_from_dict, _encode_cwd


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


# ── fixtures per real layout ────────────────────────────────────────────────

def _mk_claude_global(home: Path):
    # global config lives in ~/.claude.json (NEXT TO ~/.claude/)
    (home / ".claude.json").write_text(json.dumps({
        "mcpServers": {
            "filesystem": {"command": "npx", "args": ["-y", "@mcp/fs"], "env": {"TOKEN": "abc"}},
            "remote": {"url": "https://mcp.example.com/sse", "headers": {"Authorization": "Bearer x"}},
        },
        "projects": {str(home / "proj"): {"mcpServers": {}}},
    }), encoding="utf-8")
    cd = home / ".claude"
    (cd / "skills" / "web-search").mkdir(parents=True)
    (cd / "settings.json").write_text("{}", encoding="utf-8")
    return cd


def _mk_claude_project(home: Path):
    proj = home / "proj"
    proj.mkdir()
    (proj / "CLAUDE.md").write_text("You are a project Claude agent.\n- prefers Python", encoding="utf-8")
    (proj / ".env").write_text("OPENAI_API_KEY=sk-secret\n", encoding="utf-8")
    # a session transcript under ~/.claude/projects/<encoded cwd>/
    sess = home / ".claude" / "projects" / _encode_cwd(proj)
    sess.mkdir(parents=True)
    (sess / "s1.jsonl").write_text(
        json.dumps({"message": {"role": "user", "content": "hello from a past session"}}) + "\n",
        encoding="utf-8",
    )
    return proj


def _mk_codex(home: Path):
    d = home / ".codex"
    (d / "skills" / "coder").mkdir(parents=True)
    (d / "memories").mkdir(parents=True)
    (d / "AGENTS.md").write_text("Codex agent instructions.", encoding="utf-8")
    (d / "config.toml").write_text(
        'model = "gpt"\n[mcp_servers.fs]\ncommand = "npx"\nargs = ["-y", "srv"]\n', encoding="utf-8")
    (d / "memories" / "notes.md").write_text("- remembers X", encoding="utf-8")
    return d


def _mk_openclaw(home: Path):
    d = home / ".openclaw"
    (d / "workspace").mkdir(parents=True)
    (d / "skills" / "s").mkdir(parents=True)
    (d / "openclaw.json").write_text("{}", encoding="utf-8")
    (d / "workspace" / "SOUL.md").write_text("I am OpenClaw.", encoding="utf-8")
    (d / "workspace" / "MEMORY.md").write_text("- lives in Beijing", encoding="utf-8")
    return d


# ── detection ────────────────────────────────────────────────────────────────

def test_detect_all(home):
    _mk_claude_global(home)
    _mk_codex(home)
    _mk_openclaw(home)
    found = {d.framework for d in detector.detect_all(home)}
    assert {"claude_code", "codex", "openclaw"} <= found


# ── claude: global vs project ────────────────────────────────────────────────

def test_scan_claude_global(home):
    cd = _mk_claude_global(home)
    r = scanner.scan(path=cd)
    assert r.source.framework == "claude_code"
    # global: skills + mcp from ~/.claude.json (stdio env + url headers carried)
    assert {s.name for s in r.skills} == {"web-search"}
    by = {m.name: m for m in r.mcp_servers}
    assert by["filesystem"].env == {"TOKEN": "abc"}
    assert by["remote"].transport == "url"


def test_scan_claude_project_with_session_seed(home):
    _mk_claude_global(home)
    proj = _mk_claude_project(home)
    r = scanner.scan(path=proj, framework="claude_code")
    assert "project Claude agent" in r.agent.system_prompt      # CLAUDE.md → Awareness
    assert "hello from a past session" in r.session_summary_seed  # sessions = ours
    assert "OPENAI_API_KEY" in r.custom.credential_keys          # keys only, no value
    assert not any("sk-secret" in c for c in r.custom.credential_keys)


# ── codex: config.toml mcp + memories ────────────────────────────────────────

def test_scan_codex(home):
    d = _mk_codex(home)
    r = scanner.scan(path=d, framework="codex")
    assert "Codex agent instructions" in r.agent.system_prompt
    assert any(m.name == "fs" and m.command == "npx" for m in r.mcp_servers)  # config.toml
    assert any("remembers X" in m.content for m in r.memory)                   # memories/*.md
    assert {s.name for s in r.skills} == {"coder"}


# ── openclaw: persona/memory under workspace/ ────────────────────────────────

def test_scan_openclaw_workspace(home):
    d = _mk_openclaw(home)
    r = scanner.scan(path=d, framework="openclaw")
    assert r.agent.system_prompt == "I am OpenClaw."             # workspace/SOUL.md
    assert any("lives in Beijing" in m.content for m in r.memory)  # workspace/MEMORY.md


# ── helpers ──────────────────────────────────────────────────────────────────

def test_memory_from_md_bullets_vs_prose():
    facts = _memory_from_md("- a\n- b", "MEMORY.md")
    assert [m.content for m in facts] == ["a", "b"]
    assert _memory_from_md("just prose", "MEMORY.md")[0].type == "note"


def test_mcp_from_dict_shapes():
    m = _mcp_from_dict({
        "a": {"command": "x", "args": ["y"], "env": {"K": "v"}},
        "b": {"url": "https://h", "headers": {"H": "1"}},
        "junk": "not-a-dict",
    })
    by = {s.name: s for s in m}
    assert by["a"].transport == "stdio" and by["a"].env == {"K": "v"}
    assert by["b"].transport == "url"
    assert "junk" not in by
