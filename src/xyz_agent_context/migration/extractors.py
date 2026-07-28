"""
@file_name: extractors.py
@author: NetMind.AI
@date: 2026-07-21
@description: Per-framework extraction into the standardized migration JSON.

Source layouts verified against Hermes `agent_import.py` /
`openclaw_to_hermes.py` (Nous Research, MIT) — the detect+extract rules are
mirrored (reimplemented in our style + conventions, with attribution). The
map+write half and the session→Narrative step are ours.

Real per-framework layouts:
- Claude Code: MCP in ``~/.claude.json`` mcpServers (+ settings.json); agent
  instructions in ``<cwd>/CLAUDE.md``; global skills in ``~/.claude/skills/``;
  SESSIONS in ``~/.claude/projects/<encoded-cwd>/*.jsonl`` (ours to use).
- Codex: ``~/.codex/AGENTS.md`` + ``config.toml`` mcp_servers + ``memories/*.md``
  + ``skills/``.
- OpenClaw: persona/memory under ``~/.openclaw/workspace/{SOUL,MEMORY,USER}.md``
  + ``workspace/memory/``; ``skills/`` (+ shared); ``openclaw.json`` mcp.
- Hermes: ``~/.hermes/{SOUL,MEMORY,USER}.md``, config.yaml, skills/.

Best-effort: a missing/malformed file degrades to empty, never raises.
Credential policy (Owner): MCP env/header VALUES are carried; other secrets
contribute KEY NAMES only to custom.credential_keys.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Tuple

from loguru import logger

from xyz_agent_context.schema.migration_schema import (
    Framework,
    MigrationAgent,
    MigrationCustom,
    MigrationMcpServer,
    MigrationMemory,
    MigrationSkill,
)

# Cap a single memory entry so a huge doc doesn't dominate (mirrors Hermes).
_MEMORY_ENTRY_CHAR_LIMIT = 20_000
# How many recent sessions to fold into the self-summarize seed.
_SESSION_SEED_MAX_FILES = 5
_SESSION_SEED_CHAR_LIMIT = 12_000


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return ""


def _first_existing(base: Path, names: List[str]) -> Tuple[str, str]:
    for n in names:
        p = base / n
        if p.exists() and p.is_file():
            return _read(p), n
    return "", ""


def _memory_from_md(text: str, source_file: str) -> List[MigrationMemory]:
    """Bullet lines → one fact each; otherwise the whole doc → one note."""
    if not text.strip():
        return []
    bullets = [
        re.sub(r"^\s*[-*]\s+", "", ln).strip()
        for ln in text.splitlines()
        if re.match(r"^\s*[-*]\s+\S", ln)
    ]
    if bullets:
        return [MigrationMemory(type="fact", content=b[:_MEMORY_ENTRY_CHAR_LIMIT],
                                source_file=source_file) for b in bullets]
    return [MigrationMemory(type="note", content=text.strip()[:_MEMORY_ENTRY_CHAR_LIMIT],
                            source_file=source_file)]


def _memory_from_dir(mem_dir: Path) -> List[MigrationMemory]:
    """Each *.md under a memory/ dir contributes memory entries."""
    out: List[MigrationMemory] = []
    if mem_dir.exists() and mem_dir.is_dir():
        for f in sorted(mem_dir.glob("*.md")):
            out += _memory_from_md(_read(f), f"{mem_dir.name}/{f.name}")
    return out


# Secret-bearing patterns for MCP fields. MCP creds hide in args/url too, not
# just env/headers (e.g. `--api-key=...`, `?token=`, `sk-...`). We flag which
# fields carry a secret so the plaintext preview can highlight + warn.
_SECRET_RE = re.compile(
    r"(api[_-]?key|secret|token|password|passwd|bearer|authorization|access[_-]?key"
    r"|sk-[A-Za-z0-9]|v2\.[A-Za-z0-9_]{20})",
    re.IGNORECASE,
)


def _looks_secret(text: str) -> bool:
    return bool(text) and bool(_SECRET_RE.search(text))


def _flag_secret_fields(srv: MigrationMcpServer) -> None:
    """Populate srv.secret_fields with the dotted paths that carry a secret."""
    fields: List[str] = []
    # _looks_secret already scans the whole URL (query included), so no separate
    # query check is needed.
    if srv.url and _looks_secret(srv.url):
        fields.append("url")
    for i, a in enumerate(srv.args):
        if _looks_secret(a):
            fields.append(f"args[{i}]")
    for k, v in srv.env.items():
        if _looks_secret(k) or _looks_secret(v):
            fields.append(f"env.{k}")
    for k, v in srv.headers.items():
        if _looks_secret(k) or _looks_secret(v):
            fields.append(f"headers.{k}")
    srv.secret_fields = fields


def _mcp_from_dict(servers: Dict) -> List[MigrationMcpServer]:
    """A ``name -> cfg`` map (Claude .claude.json / .mcp.json, Codex config.toml
    mcp_servers, ...) → MigrationMcpServer list. stdio + url shapes."""
    out: List[MigrationMcpServer] = []
    if not isinstance(servers, dict):
        return out
    for name, cfg in servers.items():
        if not isinstance(cfg, dict):
            continue
        if cfg.get("url"):
            out.append(MigrationMcpServer(
                name=str(name), transport="url", url=cfg.get("url"),
                headers={str(k): str(v) for k, v in (cfg.get("headers") or {}).items()},
            ))
        elif cfg.get("command"):
            out.append(MigrationMcpServer(
                name=str(name), transport="stdio", command=str(cfg["command"]),
                args=[str(a) for a in (cfg.get("args") or [])],
                env={str(k): str(v) for k, v in (cfg.get("env") or {}).items()},
            ))
    for srv in out:
        _flag_secret_fields(srv)
    return out


def _load_json(path: Path) -> Dict:
    try:
        return json.loads(_read(path)) or {}
    except Exception:  # noqa: BLE001
        return {}


def _skills_from_dir(skills_dir: Path, source: str) -> List[MigrationSkill]:
    """Each immediate subdirectory of a skills/ folder is one skill."""
    out: List[MigrationSkill] = []
    if skills_dir.exists() and skills_dir.is_dir():
        for child in sorted(skills_dir.iterdir()):
            if child.is_dir():
                out.append(MigrationSkill(name=child.name, source=source))
    return out


def _env_keys(base: Path) -> List[str]:
    """KEY names from a .env (values NEVER read)."""
    keys = []
    for ln in _read(base / ".env").splitlines():
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", ln)
        if m:
            keys.append(m.group(1))
    return keys


def _encode_cwd(cwd: Path) -> str:
    """Claude Code encodes a project's cwd into its projects/ dir name by
    replacing path separators with '-' (e.g. /Users/x/Downloads →
    -Users-x-Downloads)."""
    return str(cwd).replace("/", "-")


def _claude_session_seed(claude_home: Path, cwd: Path) -> str:
    """Fold the most recent session transcripts for a project into a compact
    seed the agent later self-summarizes into a Narrative. Ours — Hermes does
    not import sessions. Best-effort; empty if none."""
    sess_dir = claude_home / "projects" / _encode_cwd(cwd)
    if not sess_dir.exists():
        return ""
    files = sorted(sess_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    chunks: List[str] = []
    total = 0
    for f in files[:_SESSION_SEED_MAX_FILES]:
        # Pull just the text of user/assistant messages, newest sessions first.
        for ln in _read(f).splitlines():
            try:
                obj = json.loads(ln)
            except Exception:  # noqa: BLE001
                continue
            msg = obj.get("message") if isinstance(obj, dict) else None
            content = ""
            if isinstance(msg, dict):
                c = msg.get("content")
                if isinstance(c, str):
                    content = c
                elif isinstance(c, list):
                    content = " ".join(
                        b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text"
                    )
            if content.strip():
                chunks.append(content.strip())
                total += len(content)
        if total >= _SESSION_SEED_CHAR_LIMIT:
            break
    return "\n".join(chunks)[:_SESSION_SEED_CHAR_LIMIT]


# ── per-framework extractors ────────────────────────────────────────────────
# Each returns (agent, skills, memory, mcp_servers, custom, session_seed).

def _extract_claude_code(base: Path):
    """Two shapes:
    - base == ~/.claude (global): global skills + global mcp (~/.claude.json).
    - base == a project cwd: CLAUDE.md → system_prompt; project + global mcp;
      project skills; session seed from ~/.claude/projects/<encoded cwd>.
    """
    home = Path.home()
    claude_home = home / ".claude"
    claude_json = _load_json(home / ".claude.json")
    global_mcp = _mcp_from_dict(claude_json.get("mcpServers") or {})
    # also settings.json mcpServers (secondary)
    global_mcp += _mcp_from_dict(_load_json(claude_home / "settings.json").get("mcpServers") or {})

    is_global = base.resolve() == claude_home.resolve()
    if is_global:
        agent = MigrationAgent(name="Claude Code Agent",
                               system_prompt=_first_existing(base, ["CLAUDE.md"])[0])
        skills = _skills_from_dir(base / "skills", "claude_code")
        # list projects as candidates (info) so the user can rescan a project cwd
        projects = list((claude_json.get("projects") or {}).keys())
        custom = MigrationCustom(
            credential_keys=_env_keys(base),
            llm_fallback_notes=(
                f"Global Claude config. {len(projects)} project(s) available — "
                f"rescan a specific project cwd to import its CLAUDE.md + sessions: "
                + ", ".join(projects[:10])
            ) if projects else "",
        )
        return agent, skills, [], global_mcp, custom, ""

    # project cwd
    sys_prompt, _ = _first_existing(base, ["CLAUDE.md", "CLAUDE.local.md"])
    agent = MigrationAgent(name=base.name or "Claude Code Agent", system_prompt=sys_prompt)
    mcp = list(global_mcp)
    mcp += _mcp_from_dict(_load_json(base / ".mcp.json").get("mcpServers") or {})
    proj_cfg = (claude_json.get("projects") or {}).get(str(base.resolve()), {})
    mcp += _mcp_from_dict(proj_cfg.get("mcpServers") or {})
    skills = _skills_from_dir(base / ".claude" / "skills", "claude_code_project")
    skills += _skills_from_dir(claude_home / "skills", "claude_code_global")
    seed = _claude_session_seed(claude_home, base)
    custom = MigrationCustom(credential_keys=_env_keys(base))
    return agent, skills, [], mcp, custom, seed


def _extract_soul_based(base: Path, fw: Framework):
    """Hermes / OpenClaw. OpenClaw persona/memory live under workspace/;
    Hermes at the root. Try both."""
    ws_candidates = [base / "workspace", base / "workspace.default", base]
    ws = next((w for w in ws_candidates if (w / "SOUL.md").exists()), base)
    sys_prompt, _ = _first_existing(ws, ["SOUL.md", "AGENTS.md"])
    agent = MigrationAgent(name=(base.name.lstrip(".") or fw), system_prompt=sys_prompt)
    mem_text, mem_file = _first_existing(ws, ["MEMORY.md"])
    memory = _memory_from_md(mem_text, mem_file)
    memory += _memory_from_dir(ws / "memory")
    user_text, user_file = _first_existing(ws, ["USER.md"])
    if user_text.strip():
        memory.append(MigrationMemory(type="profile", content=user_text.strip()[:_MEMORY_ENTRY_CHAR_LIMIT],
                                      source_file=user_file))
    skills = _skills_from_dir(base / "skills", fw)
    mcp: List[MigrationMcpServer] = []
    for cfg_name in ("openclaw.json", "clawdbot.json", "moltbot.json"):
        cfg = _load_json(base / cfg_name)
        mcp += _mcp_from_dict(cfg.get("mcpServers") or cfg.get("mcp_servers") or {})
    custom = MigrationCustom(credential_keys=_env_keys(base))
    return agent, skills, memory, mcp, custom, ""


def _extract_codex(base: Path):
    sys_prompt, _ = _first_existing(base, ["AGENTS.md", "instructions.md"])
    agent = MigrationAgent(name="Codex Agent", system_prompt=sys_prompt)
    # config.toml → mcp_servers (TOML)
    mcp: List[MigrationMcpServer] = []
    toml_path = base / "config.toml"
    if toml_path.exists():
        try:
            import tomllib
            data = tomllib.loads(_read(toml_path))
            mcp = _mcp_from_dict(data.get("mcp_servers") or {})
        except Exception as e:  # noqa: BLE001
            logger.warning(f"codex config.toml parse failed: {e}")
    memory = _memory_from_dir(base / "memories")
    skills = _skills_from_dir(base / "skills", "codex")
    custom = MigrationCustom(credential_keys=_env_keys(base))
    return agent, skills, memory, mcp, custom, ""


def _extract_custom(base: Path):
    sys_prompt, _ = _first_existing(base, ["AGENTS.md", "CLAUDE.md", "SOUL.md"])
    agent = MigrationAgent(name=base.name.lstrip(".") or "imported-agent", system_prompt=sys_prompt)
    unmapped = [p.name for p in base.iterdir() if p.is_file()] if base.exists() else []
    custom = MigrationCustom(
        unmapped_files=unmapped,
        credential_keys=_env_keys(base),
        llm_fallback_notes="Custom framework — LLM fallback mapping recommended.",
    )
    return agent, [], [], [], custom, ""


def extract(framework: Framework, path: str | Path):
    """Dispatch. Returns (agent, skills, memory, mcp_servers, custom, session_seed)."""
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
        return MigrationAgent(), [], [], [], MigrationCustom(llm_fallback_notes=str(e)), ""
