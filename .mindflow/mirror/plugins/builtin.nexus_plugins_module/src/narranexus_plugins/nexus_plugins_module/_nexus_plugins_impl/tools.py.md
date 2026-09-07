---
code_file: plugins/builtin.nexus_plugins_module/src/narranexus_plugins/nexus_plugins_module/_nexus_plugins_impl/tools.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-03（批 2f.1）— 16 个 plugin_* MCP 工具

对 `SelfExtensionService` 的薄包装：返回 JSON 字符串，异常变 `{"error": ...}`，永不 traceback。

## 2026-09-04 · no per-module port (batch 5a)

The MCP server URL comes from `mcp_server_url("<server_name>")` (the single MCP host + `/mcp/<server_name>/sse`); the module-level port constant / `self.port` and the factory's `port` parameter are gone.

2026-09-07: awareness tools `platform_overview`, `platform_slots`, `contract_docs`, `agent_self`, `capability_set` (async helpers `_arun`/`_db`).

## 2026-09-07 — honest tool signatures; per-tool cloud guard

platform_overview / platform_slots / contract_docs no longer take agent_id/user_id they never used (a signature is the model's contract; spare parameters invite invented values); capability_set takes no user_id (identity comes from caller_user_id_from_request). All five awareness tools refuse on the cloud deployment themselves (GuardError) instead of relying on the server never being mounted there.
