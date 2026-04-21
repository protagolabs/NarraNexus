---
code_file: src/xyz_agent_context/module/lark_module/lark_module.py
stub: false
last_verified: 2026-04-21
---

## 2026-04-21 update — strengthened `lark_cli` quick-reference in rendered prompt

After an incident where an agent sent the literal string
`./news_content.md` to a colleague (it interpreted `--markdown` as a
file-path flag), the `skill_section` inside `get_instructions()` now:

- **Promotes `lark_skill` from "recommended" to "CRITICAL must-call"** for
  any domain not yet loaded this session. The section leads with a code
  fence showing the exact MCP tool call.
- **Adds a "Quick reference — easiest flags to misuse" block** covering
  `--text` / `--markdown` (inline body, not paths), `--file`, `--as bot`
  vs `--as user`, and `--chat-id` vs `--user-id`. The block is
  explicitly labelled as a reminder only — agents must still read the
  full SKILL.md via `lark_skill` for authoritative syntax.
- **Mentions `mcp__lark_module__lark_skill` by exact tool name** so the
  model can route straight to it.

This is pure prompt work — the actual CLI flag semantics live in the
lark-skill repo (official, not editable from here). The change only
tightens how strongly we steer the agent toward reading those docs
before acting.

## Why it exists

Entry point for the Lark/Feishu integration.  Registers the module with
the framework, creates the MCP server, injects Lark credential info
into the agent's context, and registers a channel sender so other
modules can send Lark messages on behalf of an agent.

## Design decisions

- **`module_type = "capability"`** — auto-loaded for every agent; no
  LLM judgment needed to activate.  The module contributes context and
  MCP tools regardless of whether a bot is bound.
- **MCP port 7830** — chosen to avoid collision with MessageBusModule
  (7820) and earlier modules (7801-7806).
- **`ChannelSenderRegistry.register("lark", ...)`** — class-level
  `_sender_registered` flag ensures the sender is registered exactly
  once across all LarkModule instances.
- **`get_config()` is `@staticmethod`** — matches the framework contract
  where `MODULE_MAP` may call it without an instance.

## Upstream / downstream

- **Upstream**: `module/__init__.py` (MODULE_MAP), `module_service.py`.
- **Downstream**: `_lark_mcp_tools.py`, `_lark_credential_manager.py`
  (hook_data_gathering), `ChannelSenderRegistry` (send function).

## Gotchas

- `hook_after_event_execution` compares `str(ws)` against
  `WorkingSource.LARK.value` because `working_source` may arrive as
  either the enum or its string representation.
