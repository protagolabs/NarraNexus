---
code_file: src/xyz_agent_context/migration/extractors.py
stub: false
last_verified: 2026-07-21
---

## Why it exists

Per-framework extraction of a source config into the standardized JSON
dimensions (agent / skills / memory / mcp + session seed). Best-effort: a
missing/malformed file degrades to empty, never raises (a scan must not crash).

## Real source layouts (verified vs Hermes agent_import.py / openclaw_to_hermes.py, MIT)

- **Claude Code**: MCP is in `~/.claude.json` mcpServers (+ `settings.json`) — NOT
  `~/.claude/.mcp.json`. Instructions live per-project in `<cwd>/CLAUDE.md`;
  global skills in `~/.claude/skills/`; per-project config (incl. mcpServers) in
  `~/.claude.json` `projects[<cwd>]`. Two shapes handled: `~/.claude` (global —
  skills + global mcp) vs a project cwd (CLAUDE.md → system_prompt + project +
  global mcp + project/global skills + session seed).
- **Codex**: `AGENTS.md` → system_prompt; `config.toml` `mcp_servers` (tomllib);
  `memories/*.md` → memory; `skills/`.
- **OpenClaw**: persona/memory under `workspace/{SOUL,MEMORY,USER}.md` +
  `workspace/memory/` (NOT the `~/.openclaw` root); `skills/`; `openclaw.json` mcp.
- **Hermes**: same SOUL/MEMORY/USER at the root.

## Design decisions

- **Sessions are ours** (`_claude_session_seed`): Hermes ignores session
  transcripts; we fold the newest few `~/.claude/projects/<encoded-cwd>/*.jsonl`
  message texts into `session_summary_seed` (capped) for the agent to
  self-summarize into a Narrative.
- **Secret tagging** (`_flag_secret_fields`): MCP creds hide in `args`
  (`--api-key=…`) and `url` query too, not only env/headers. We flag the dotted
  field paths (`args[i]`, `env.K`, `url`, …) in `secret_fields` so the plaintext
  preview highlights them. VALUES are carried (Owner decision); non-MCP `.env`
  secrets contribute KEY NAMES only.
- CLAUDE.md/SOUL.md/AGENTS.md → **Awareness** (Owner), not Memory (Hermes maps
  them to memory); MEMORY.md/`memories/`/USER.md → Memory.
