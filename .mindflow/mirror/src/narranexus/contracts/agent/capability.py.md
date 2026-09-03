---
code_file: src/narranexus/contracts/agent/capability.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — 能力 = 阶段参与集合 + 元数据（五级一个契约）

`CapabilityTier` 五级不是五套机制，而是同一个 `Capability` 契约填了多少格子，`TIER_STAGES` 是这张表：
Tool 只在 Act（`tools`）、ContextProvider 只在 Assemble、Skill 在 Assemble+Act、MemoryKind 在
Recall（`recall`）+Commit+Reflect、Module 除 Compose 外全部（Compose 是平台自己的阶段，没有参与方法）。
`StageParticipant` 的方法全部可选（结构化 Protocol），`STAGE_METHODS` 是「哪个方法属于哪个阶段」的
唯一真源，六个有参与方法的阶段各有一行；运行时与 `LegacyModuleAdapter` 都从它派生（测试钉住它与
Protocol 属性集相等，且每个 tier 允许的阶段都能用 `STAGE_METHODS` 表达）。预审曾指出 docstring
写「四级」而枚举有五个、Recall/Act 无行——已改正。
`CapabilityMeta` 收拢了研究文档 H1 里散在 7 张表的字段（priority/always_load/is_task/
provides_chat_history/instance_prefix/context_cost_hint）。`XYZBaseModule` 九方法的映射写在
模块 docstring。
