---
code_file: src/xyz_agent_context/module/lark_module/_lark_skill_loader.py
stub: false
last_verified: 2026-04-16
---

## Why it exists

Discovers and loads Lark CLI Skill documentation files (SKILL.md) from the
filesystem. These are registered as MCP Resources so the Agent can read
them on demand (e.g., `lark://skills/lark-im`).

## Design decisions

- **Two search paths** — `~/.claude/skills/lark-*/SKILL.md` and
  `~/.agents/skills/lark-*/SKILL.md`. Covers both Claude Code skills
  and standalone agent installations.
- **Strips YAML frontmatter** — SKILL.md files start with `---` YAML
  blocks that are irrelevant to the Agent. Only the markdown body is
  returned.
- **Lazy loading** — `get_available_skills()` scans directories once at
  import time. `load_skill_content(name)` reads the file on demand.

## Upstream / downstream

- **Upstream**: `_lark_mcp_tools_v2.py` calls `get_available_skills()` and
  `load_skill_content()` to register MCP Resources.
- **Downstream**: filesystem (reads SKILL.md files).

## Gotchas

- Skills installed after the MCP server starts are not picked up. A server
  restart is required.
- If no skills are found, a warning is logged. The Agent can still use
  `lark_cli` but won't have on-demand Skill docs.
