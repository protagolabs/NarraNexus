---
code_file: src/xyz_agent_context/module/nexus_plugins_module/nexus_plugins_module.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（批 2f.1）— 模块壳：指令块 + plugin_* MCP 工具

形状照 `SkillModule`：capability 模块、进 `CORE_ALWAYS_LOAD`、端口 7811、无状态 MCP server（工具收
agent_id/user_id）。指令块很短（一行流程 + 本 Agent 的插件状态摘要），控制每回合上下文成本。**只在本地生效**：
云端指令为空、`get_mcp_config` 返回 None、服务层直接拒绝（D1）。它自己也是插件 `builtin.nexus_plugins_module`
且 `protected`，任何工具都不能改它。
