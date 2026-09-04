---
code_file: src/xyz_agent_context/module/nexus_plugins_module/_nexus_plugins_impl/tools.py
last_verified: 2026-09-04
stub: false
---

## 2026-09-03（批 2f.1）— 16 个 plugin_* MCP 工具

对 `SelfExtensionService` 的薄包装：返回 JSON 字符串，异常变 `{"error": ...}`，永不 traceback。

## 2026-09-04 · no per-module port (batch 5a)

The MCP server URL comes from `mcp_server_url("<server_name>")` (the single MCP host + `/mcp/<server_name>/sse`); the module-level port constant / `self.port` and the factory's `port` parameter are gone.
