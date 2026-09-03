---
code_file: src/narranexus/contracts/agent/__init__.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — Agent 纵横模型的契约包（spec §7，D23）

纵轴 = 七阶段回合流水线（`stages`），横轴 = 能力（`capability`），每阶段策略由 `pipeline` 的
`PipelineProfile` 选择，整个 Agent 是 `agent_spec.AgentSpec` 一个值对象，阶段观察钩子在 `events`。
全部是值类型或结构化 Protocol：平台实现阶段，插件实现能力与策略，双方都不 import 对方。
批 1 只立契约与 `LegacyModuleAdapter`（expand）；`platform/turn/` 的阶段编排器与
`stageStrategies/pipelineProfiles/contextProviders` 三 kind 是批 3。契约版本 `API_VERSIONS["agent"]`。
