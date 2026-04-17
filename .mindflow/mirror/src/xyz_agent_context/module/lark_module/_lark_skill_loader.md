---
code_file: src/xyz_agent_context/module/lark_module/_lark_skill_loader.py
stub: false
last_verified: 2026-04-16
---

## Why it exists

Discovers and loads Lark CLI Skill documentation files (SKILL.md) from the
filesystem. Their content is returned by the `lark_skill` MCP tool in
`_lark_mcp_tools.py`, so the Agent can read them on demand before using a
new Lark domain.

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

- **Upstream**: `_lark_mcp_tools.py` calls `get_available_skills()` and
  `load_skill_content()` from inside the `lark_skill` MCP tool.
- **Upstream**: `lark_module.py` calls `get_available_skills()` in
  `get_instructions()` to enumerate them in the system prompt.
- **Downstream**: filesystem (reads SKILL.md files).

## Gotchas

- Skills are re-discovered on every `lark_skill` tool call; no caching.
  Installing new skills at runtime takes effect without a restart.
- If no skills are found, `get_instructions()` renders the no-skill
  fallback; the Agent can still use `lark_cli` but has no domain docs.
