---
code_file: src/xyz_agent_context/module/lark_module/lark_module.py
stub: false
last_verified: 2026-04-16
---

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
- **Downstream**: `_lark_mcp_tools_v2.py` (V2 MCP tools), `_lark_credential_manager.py`
  (hook_data_gathering), `ChannelSenderRegistry` (send function).

## Gotchas

- `hook_after_event_execution` compares `str(ws)` against
  `WorkingSource.LARK.value` because `working_source` may arrive as
  either the enum or its string representation.
