---
code_file: src/xyz_agent_context/module/lark_module/_lark_mcp_tools_v2.py
stub: false
last_verified: 2026-04-16
---

## Why it exists

Registers V2 Lark MCP tools on the FastMCP server. Replaces the V1 file
(`_lark_mcp_tools.py`, deleted) which had 19 individual tools. V2 exposes
5 tools + MCP Resources for Lark CLI Skill docs.

## Design decisions

- **Single `lark_cli` tool** replaces 19 purpose-specific tools. The Agent
  learns CLI commands from instructions + Skill docs rather than having a
  dedicated tool per operation. This scales to all CLI capabilities without
  code changes.
- **Security via `_lark_command_security`** — `validate_command` +
  `sanitize_command` are called before every `lark_cli` invocation.
- **Lifecycle tools** (`lark_setup`, `lark_auth`, `lark_auth_complete`,
  `lark_status`) remain dedicated because they require structured flows
  (URL extraction, device code exchange) that don't map to a single CLI
  command string.
- **`lark_auth` accepts `scopes` parameter** — when a CLI command fails
  with "missing scope", the Agent extracts the scope name and requests it
  specifically, instead of always using `--recommend`.
- **MCP Resources** register each Lark CLI Skill file as
  `lark://skills/{name}` so the Agent can read them on demand.

## Upstream / downstream

- **Upstream**: `lark_module.py` calls `register_lark_mcp_tools_v2(mcp)`.
- **Downstream**: `_lark_command_security.py` (validation),
  `_lark_credential_manager.py` (credential lookup), `lark_cli_client.py`
  (`_run_v2`), `_lark_workspace.py` (for `lark_setup`),
  `_lark_skill_loader.py` (MCP Resources).

## Gotchas

- `lark_setup` creates a credential with `app_id="pending_setup"`. This
  must be updated to the real app_id after the user completes browser
  setup. Currently handled by credential watcher or status check.
- MCP Resource registration happens at module load time (not per-request).
  If skills are installed after startup, a restart is needed.
