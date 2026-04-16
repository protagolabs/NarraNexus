---
code_file: src/xyz_agent_context/module/lark_module/_lark_service.py
stub: false
last_verified: 2026-04-16
---

## Why it exists

Shared Lark business logic that both the HTTP routes (`backend/routes/lark.py`)
and MCP tools (`_lark_mcp_tools_v2.py`) need.  Lives in the core package to
avoid circular imports — the API layer imports from here, never the other
way around.

## Design decisions

- **`do_bind()`** — single implementation of the bind flow: validate,
  register CLI profile via `config_init` (--profile based), save credential,
  fetch bot name, resolve owner. Both the HTTP route and MCP tool call this.
- **`resolve_owner()`** — looks up a Lark user by email via `_run_v2`,
  returns `(open_id, display_name)`.
- **`determine_auth_status()`** — pure function that interprets the
  lark-cli `auth status` output into one of 3 states: `bot_ready`,
  `user_logged_in`, or `not_logged_in`.

## Upstream / downstream

- **Upstream**: `backend/routes/lark.py`, `_lark_mcp_tools_v2.py`.
- **Downstream**: `LarkCLIClient` (`config_init`, `_run_v2`),
  `LarkCredentialManager`.

## Gotchas

- `do_bind` uses `_cli.config_init()` (--profile) not `_run_v2`. This is
  intentional — manual bind provides app_id/secret directly, no workspace
  needed. Only Quick Setup (`config init --new`) uses HOME isolation.
- `resolve_owner` uses `_run_v2` which maps to --profile. The owner email
  lookup requires bot identity (tenant_access_token), not user identity.
