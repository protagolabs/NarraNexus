---
code_file: src/xyz_agent_context/module/lark_module/_lark_mcp_tools.py
stub: false
last_verified: 2026-04-16
---

## Why it exists

Registers Lark MCP tools on the FastMCP server. Exposes 6 tools:
`lark_cli` (main execution), `lark_setup` / `lark_auth` / `lark_auth_complete`
(OAuth lifecycle), `lark_status` (health), and `lark_skill` (load SKILL.md
docs so the Agent learns correct command syntax before first use).

## Design decisions

- **Single `lark_cli` tool** for all domain operations. The Agent learns CLI
  commands from instructions + Skill docs rather than having a dedicated
  tool per operation. This scales to all CLI capabilities without code
  changes.
- **Security via `_lark_command_security`** — `validate_command` +
  `sanitize_command` are called before every `lark_cli` invocation.
- **Lifecycle tools** (`lark_setup`, `lark_auth`, `lark_auth_complete`,
  `lark_status`) remain dedicated because they require structured flows
  (URL extraction, device code exchange) that don't map to a single CLI
  command string.
- **`lark_auth` accepts `scopes` parameter** — when a CLI command fails
  with "missing scope", the Agent extracts the scope name and requests it
  specifically, instead of always using `--recommend`.
- **`lark_skill` exposes SKILL.md as a tool**, not a Resource. Claude Agent
  SDK surfaces MCP Tools directly to the LLM, but does not expose Resources
  as callable, so a dedicated tool is the only reliable path. Accepts both
  "im" and "lark-im" name forms. `agent_id` parameter is kept only for API
  consistency with the other Lark tools; skill content is identical across
  agents.

## Upstream / downstream

- **Upstream**: `lark_module.py` calls `register_lark_mcp_tools(mcp)`.
- **Downstream**: `_lark_command_security.py` (validation),
  `_lark_credential_manager.py` (credential lookup), `lark_cli_client.py`
  (`_run_with_agent_id`), `_lark_workspace.py` (for `lark_setup`),
  `_lark_skill_loader.py` (SKILL.md loader, called from `lark_skill`).

## Gotchas

- `lark_setup` creates a credential with `app_id="pending_setup"`. This
  must be updated to the real app_id after the user completes browser
  setup. Currently handled by credential watcher or status check.
- Skill discovery happens at each `lark_skill` call (not cached). If the
  user installs new skills after process start, they become available
  immediately — no restart needed.
