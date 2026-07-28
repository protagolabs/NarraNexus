"""
Agent Migration Scanner — framework detection + extraction into standardized JSON.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from xyz_agent_context.migration import detector, scanner
from xyz_agent_context.migration.extractors import _memory_from_md, _parse_mcp_json


# ── fixtures: fake home dirs for each framework ─────────────────────────────

def _mk_claude(home: Path) -> Path:
    d = home / ".claude"
    (d / "skills" / "web-search").mkdir(parents=True)
    (d / "skills" / "pdf").mkdir(parents=True)
    (d / "CLAUDE.md").write_text("You are a helpful Claude Code agent.\n- prefers Python", encoding="utf-8")
    (d / "settings.json").write_text("{}", encoding="utf-8")
    (d / ".mcp.json").write_text(json.dumps({
        "mcpServers": {
            "filesystem": {"command": "npx", "args": ["-y", "@mcp/fs", "/tmp"], "env": {"TOKEN": "abc"}},
            "remote": {"url": "https://mcp.example.com/sse", "headers": {"Authorization": "Bearer x"}},
        }
    }), encoding="utf-8")
    (d / ".env").write_text("OPENAI_API_KEY=sk-secret\nFOO=bar\n", encoding="utf-8")
    return d


def _mk_hermes(home: Path) -> Path:
    d = home / ".hermes"
    (d / "skills" / "coder").mkdir(parents=True)
    (d / "config.yaml").write_text("agent:\n  name: hermie\n", encoding="utf-8")
    (d / "SOUL.md").write_text("I am Hermes.", encoding="utf-8")
    (d / "MEMORY.md").write_text("- user is in Shanghai\n- likes tea", encoding="utf-8")
    (d / "USER.md").write_text("Name: Xiong", encoding="utf-8")
    return d


def _mk_codex(home: Path) -> Path:
    d = home / ".codex"
    d.mkdir(parents=True)
    (d / "AGENTS.md").write_text("Codex agent instructions.", encoding="utf-8")
    (d / "config.toml").write_text("model='gpt'", encoding="utf-8")
    return d


# ── detection ───────────────────────────────────────────────────────────────

def test_detect_all_finds_each_framework(tmp_path):
    _mk_claude(tmp_path)
    _mk_hermes(tmp_path)
    _mk_codex(tmp_path)
    found = {d.framework for d in detector.detect_all(tmp_path)}
    assert {"claude_code", "hermes", "codex"} <= found


def test_classify_path_high_confidence(tmp_path):
    d = _mk_claude(tmp_path)
    det = detector.classify_path(d)
    assert det.framework == "claude_code"
    assert det.confidence == "high"


def test_classify_unknown_is_custom(tmp_path):
    # A dir with no framework signal at all → custom fallback (low).
    # (AGENTS.md would be claimed by Codex; use a non-signal file here.)
    (tmp_path / "notes.txt").write_text("something", encoding="utf-8")
    det = detector.classify_path(tmp_path)
    assert det.framework == "custom"
    assert det.confidence == "low"


# ── extraction / scan ────────────────────────────────────────────────────────

def test_scan_claude_code(tmp_path):
    d = _mk_claude(tmp_path)
    result = scanner.scan(path=d)
    assert result.schema_version == "1.0"
    assert result.source.framework == "claude_code"
    assert "helpful Claude Code" in result.agent.system_prompt
    # skills = each subdir of skills/
    assert {s.name for s in result.skills} == {"web-search", "pdf"}
    # mcp: one stdio (with env carried) + one url (with headers carried)
    by_name = {m.name: m for m in result.mcp_servers}
    assert by_name["filesystem"].transport == "stdio"
    assert by_name["filesystem"].command == "npx"
    assert by_name["filesystem"].env == {"TOKEN": "abc"}          # MCP creds carried
    assert by_name["remote"].transport == "url"
    assert by_name["remote"].headers.get("Authorization") == "Bearer x"
    # non-MCP secrets: KEY names only, values never extracted
    assert "OPENAI_API_KEY" in result.custom.credential_keys
    assert not any("sk-secret" in c for c in result.custom.credential_keys)


def test_scan_hermes_memory_and_soul(tmp_path):
    d = _mk_hermes(tmp_path)
    result = scanner.scan(path=d, framework="hermes")
    assert result.source.framework == "hermes"
    assert result.agent.system_prompt == "I am Hermes."
    contents = {m.content for m in result.memory}
    assert "user is in Shanghai" in contents          # MEMORY.md bullets → facts
    assert "likes tea" in contents
    assert any(m.type == "profile" for m in result.memory)  # USER.md → profile


def test_scan_autodetect_picks_highest_confidence(tmp_path, monkeypatch):
    _mk_hermes(tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    result = scanner.scan()  # no path → auto-detect
    assert result.source.framework == "hermes"


def test_scan_no_source_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))  # empty
    with pytest.raises(FileNotFoundError):
        scanner.scan()


# ── helpers ──────────────────────────────────────────────────────────────────

def test_memory_from_md_bullets_vs_prose():
    facts = _memory_from_md("- a\n- b", "MEMORY.md")
    assert [m.content for m in facts] == ["a", "b"]
    prose = _memory_from_md("just a paragraph", "MEMORY.md")
    assert len(prose) == 1 and prose[0].type == "note"


def test_parse_mcp_json_malformed_is_empty():
    assert _parse_mcp_json("not json {") == []
    assert _parse_mcp_json("") == []
