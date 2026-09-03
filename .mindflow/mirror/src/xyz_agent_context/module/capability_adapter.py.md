---
code_file: src/xyz_agent_context/module/capability_adapter.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — `LegacyModuleAdapter`：把现有 module 看成 `Capability`（迁移的 expand 步）

不改任何 module 的语义，只把九个生命周期方法映射到契约命名的阶段参与（映射表在文件头）；
运行时与测试可以把遗留 module 和原生能力一视同仁，内置 module 在批 5 逐个改写为原生形态后本文件
删除（contract 步）。`contribute_tools` 把 `get_mcp_config`（`MCPServerConfig.server_name` 为键）与
expressive/disallowed 三者合成一个 `ToolSurface`。`tests/module/test_capability_adapter.py` 用真实
`ChatModule` + sqlite 夹具证明经适配器调用与直接调用结果相同。
