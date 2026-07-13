---
code_file: src/xyz_agent_context/module/office_module/_office_impl/_office_command_security.py
last_verified: 2026-07-13
stub: false
---

# _office_command_security.py — validate/sanitize gate for the office_cli passthrough

## Why it exists

The agent drives officecli through a single `office_cli(command)` passthrough
(same shape as lark's `lark_cli`). This file is the gate between the raw command
string and the subprocess.

The **real** injection defence is that `sanitize_command` `shlex`-splits the
string into an argv array and [[officecli_client]] runs it with `shell=False` —
no shell ever interprets the tokens (same reasoning as
[[_lark_command_security]]). On top of that argv safety, it blocks a small set
of subcommands that don't belong in a doc-editing passthrough.

## Upstream / Downstream

- **Called by:** [[_office_mcp_tools]] `office_cli` (`sanitize_command`).
- **Depends on:** stdlib `shlex` only.

## Design decisions

**Blocklist, not allowlist, for verbs.** Everything (create / view / get /
query / set / add / remove / move / … and the `docx|xlsx|pptx <verb>`
format-prefixed forms) is allowed except a handful of blocked subcommands:

- `install` / `config` — mutate global binary / config state, not a doc op.
- `mcp` — starts an MCP server (blocks until timeout).
- `watch` — starts a live-preview HTTP server (blocks). Agents should use
  `office_render` for a **static** preview instead.

The subcommand is the first non-flag token (leading global flags are skipped).

## Gotchas

- A blocked command raises `ValueError` from `sanitize_command`; the MCP tool
  catches it and returns `{success:false, error}` — the agent sees a readable
  reason, not an opaque failure.
- This gate does not police file paths — path confinement to the agent workspace
  is enforced downstream in [[officecli_client]] (CWD + realpath checks) and in
  [[registration]] at register time.
