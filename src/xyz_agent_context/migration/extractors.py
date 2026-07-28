"""
@file_name: extractors.py
@author: NetMind.AI
@date: 2026-07-21
@description: Per-framework extraction into the standardized migration JSON.

Each extractor reads a source directory's well-known files and populates the
StandardizedAgentImport dimensions (agent/skills/memory/mcp). Best-effort:
a missing/malformed file degrades to empty, never raises. Mirrors Hermes
`import-agent`'s file→dimension rules.

Credential policy: MCP env/header VALUES are carried (Owner decision); other
`.env` secrets contribute KEY NAMES only to custom.credential_keys.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List, Tuple

from loguru import logger

from xyz_agent_context.schema.migration_schema import (
    Framework,
    MigrationAgent,
    MigrationCustom,
    MigrationMcpServer,
    MigrationMemory,
    MigrationSkill,
)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — best-effort
        return ""


def _first_existing(base: Path, names: List[str]) -> Tuple[str, str]:
    """Return (content, filename) of the first existing file, else ('','')."""
    for n in names:
        p = base / n
        if p.exists() and p.is_file():
            return _read(p), n
    return "", ""


def _memory_from_md(text: str, source_file: str) -> List[MigrationMemory]:
    """Split a MEMORY.md-style doc into memory entries. Bullet lines become one
    fact each; otherwise the whole doc is one entry. Original text preserved."""
    if not text.strip():
        return []
    bullets = [
        re.sub(r"^\s*[-*]\s+", "", ln).strip()
        for ln in text.splitlines()
        if re.match(r"^\s*[-*]\s+\S", ln)
    ]
    if bullets:
        return [MigrationMemory(type="fact", content=b, source_file=source_file) for b in bullets]
    return [MigrationMemory(type="note", content=text.strip(), source_file=source_file)]


def _parse_mcp_json(text: str) -> List[MigrationMcpServer]:
    """Parse a Claude-Code-style `.mcp.json` (or the mcpServers block of any
    JSON config) into MigrationMcpServer list. Handles stdio + url shapes."""
    if not text.strip():
        return []
    try:
        data = json.loads(text)
    except Exception:  # noqa: BLE001
        return []
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        # Some configs put the map at top level.
        servers = data if isinstance(data, dict) else {}
    out: List[MigrationMcpServer] = []
    for name, cfg in servers.items():
        if not isinstance(cfg, dict):
            continue
        if cfg.get("url"):
            out.append(MigrationMcpServer(
                name=name, transport="url",
                url=cfg.get("url"),
                headers={str(k): str(v) for k, v in (cfg.get("headers") or {}).items()},
            ))
        elif cfg.get("command"):
            out.append(MigrationMcpServer(
                name=name, transport="stdio",
                command=str(cfg["command"]),
                args=[str(a) for a in (cfg.get("args") or [])],
                env={str(k): str(v) for k, v in (cfg.get("env") or {}).items()},
            ))
    return out


def _skills_from_dir(skills_dir: Path, source: str) -> List[MigrationSkill]:
    """Each immediate subdirectory of a skills/ folder is one skill."""
    if not skills_dir.exists() or not skills_dir.is_dir():
        return []
    out: List[MigrationSkill] = []
    for child in sorted(skills_dir.iterdir()):
        if child.is_dir():
            out.append(MigrationSkill(name=child.name, source=source))
    return out


def _env_keys(base: Path) -> List[str]:
    """KEY names from a .env (values NEVER read)."""
    text = _read(base / ".env")
    keys = []
    for ln in text.splitlines():
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", ln)
        if m:
            keys.append(m.group(1))
    return keys


# ── per-framework extractors ────────────────────────────────────────────────
# Each returns (agent, skills, memory, mcp_servers, custom).

def _extract_claude_code(base: Path):
    sys_prompt, sp_file = _first_existing(base, ["CLAUDE.md", "CLAUDE.local.md"])
    agent = MigrationAgent(name=base.parent.name if base.name == ".claude" else base.name,
                           system_prompt=sys_prompt)
    mcp = _parse_mcp_json(_read(base / ".mcp.json"))
    skills = _skills_from_dir(base / "skills", source="claude_code")
    custom = MigrationCustom(credential_keys=_env_keys(base))
    return agent, skills, [], mcp, custom


def _extract_soul_based(base: Path, fw: Framework):
    """Shared extractor for Hermes / OpenClaw (SOUL.md + MEMORY.md + skills/)."""
    sys_prompt, _ = _first_existing(base, ["SOUL.md", "AGENTS.md"])
    name = base.name.lstrip(".") or fw
    agent = MigrationAgent(name=name, system_prompt=sys_prompt)
    mem_text, mem_file = _first_existing(base, ["MEMORY.md"])
    memory = _memory_from_md(mem_text, mem_file)
    user_text, user_file = _first_existing(base, ["USER.md"])
    if user_text.strip():
        memory.append(MigrationMemory(type="profile", content=user_text.strip(), source_file=user_file))
    skills = _skills_from_dir(base / "skills", source=fw)
    # MCP: Hermes/OpenClaw keep it in config.yaml / openclaw.json — parse JSON forms.
    mcp: List[MigrationMcpServer] = []
    for cfg_name in ("openclaw.json", "clawdbot.json", "moltbot.json"):
        mcp += _parse_mcp_json(_read(base / cfg_name))
    custom = MigrationCustom(credential_keys=_env_keys(base))
    return agent, skills, memory, mcp, custom


def _extract_codex(base: Path):
    sys_prompt, _ = _first_existing(base, ["AGENTS.md", "instructions.md"])
    agent = MigrationAgent(name="codex-agent", system_prompt=sys_prompt)
    custom = MigrationCustom(credential_keys=_env_keys(base))
    return agent, [], [], [], custom


def _extract_custom(base: Path):
    sys_prompt, sp_file = _first_existing(base, ["AGENTS.md", "CLAUDE.md", "SOUL.md"])
    agent = MigrationAgent(name=base.name.lstrip(".") or "imported-agent", system_prompt=sys_prompt)
    unmapped = [p.name for p in base.iterdir() if p.is_file()] if base.exists() else []
    custom = MigrationCustom(
        unmapped_files=unmapped,
        credential_keys=_env_keys(base),
        llm_fallback_notes="Custom framework — LLM fallback mapping recommended.",
    )
    return agent, [], [], [], custom


def extract(framework: Framework, path: str | Path):
    """Dispatch to the right extractor. Returns
    (agent, skills, memory, mcp_servers, custom)."""
    base = Path(path).expanduser()
    try:
        if framework == "claude_code":
            return _extract_claude_code(base)
        if framework in ("hermes", "openclaw"):
            return _extract_soul_based(base, framework)
        if framework == "codex":
            return _extract_codex(base)
        return _extract_custom(base)
    except Exception as e:  # noqa: BLE001 — extraction must never crash a scan
        logger.warning(f"migration.extract({framework}, {base}) failed: {e}")
        return MigrationAgent(), [], [], [], MigrationCustom(llm_fallback_notes=str(e))
