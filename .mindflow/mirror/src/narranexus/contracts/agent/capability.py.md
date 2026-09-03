---
code_file: src/narranexus/contracts/agent/capability.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — 能力 = 阶段参与集合 + 元数据（四级一个契约）

`CapabilityTier` 四级不是四套机制，而是同一个 `Capability` 契约填了多少格子：Tool 只在 Act、
ContextProvider/Skill 只在 Assemble、MemoryKind 在 Recall+Commit+Reflect、Module 任意。
`StageParticipant` 的方法全部可选（结构化 Protocol），`STAGE_METHODS` 是「哪个方法属于哪个阶段」的
唯一真源，运行时与 `LegacyModuleAdapter` 都从它派生（测试钉住它与 Protocol 属性集相等）。
`CapabilityMeta` 收拢了研究文档 H1 里散在 7 张表的字段（priority/always_load/is_task/
provides_chat_history/instance_prefix/context_cost_hint）。`XYZBaseModule` 九方法的映射写在
模块 docstring。
