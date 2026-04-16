---
code_file: src/xyz_agent_context/module/lark_module/lark_cli_client.py
stub: false
last_verified: 2026-04-16
---

## Why it exists

Unified async wrapper around all `lark-cli` subprocess calls.
Provides two runners (`_run` with `--profile`, `_run_v2` which resolves
the profile from agent_id) plus a small set of typed business methods
still used by internal callers.

## Design decisions

- **`--profile` as primary isolation** — `_run_v2(args, agent_id)` maps
  agent_id to `agent_{agent_id}` and delegates to `_run()`.
- **HOME isolation only for `config init --new`** — `_run_with_home` sets
  HOME to the agent workspace. Used exclusively for the interactive new-app
  setup flow (which doesn't support `--profile`). All other commands use
  `--profile`.
- **`shell=False` everywhere** — args passed as list; no shell injection.
- **`--app-secret-stdin`** — secrets via stdin, never CLI args.
- **Timeout kill** — on `asyncio.TimeoutError`, subprocess is killed.
- **Minimal business methods** — only `config_init`, `profile_remove`,
  `get_user`, `send_message`, `list_chat_messages` survive from V1.
  All other Lark operations go through the generic `lark_cli` MCP tool.

## Upstream / downstream

- **Upstream**: `_lark_mcp_tools_v2.py` (via `_run_v2`),
  `_lark_service.py` (`config_init`), `lark_trigger.py` (`get_user`),
  `lark_context_builder.py` (`list_chat_messages`),
  `lark_module.py` (`send_message`), `backend/routes/lark.py`
  (`_run_v2`, `profile_remove`).
- **Downstream**: `lark-cli` binary, `_lark_workspace.py` (for HOME env).

## Gotchas

- Debug log at `_run` logs the full command. Sensitive data must go
  through `stdin_data`, never in `args`.
- `_run_with_home` inherits the full parent environment and only overrides
  HOME. On macOS this can trigger Keychain prompts — hence the limited
  use for `config init --new` only.
