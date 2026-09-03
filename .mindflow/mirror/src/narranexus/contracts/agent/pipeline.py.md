---
code_file: src/narranexus/contracts/agent/pipeline.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — `PipelineProfile`：把 fast/voice/job/silent 散开关收成一等对象

一个 profile = 每阶段策略名 + `Budgets` + `CapabilityFilter` + 叙事持久化模式；`TurnOverride`
是回合级覆盖（六层绑定的 TURN 层），`with_override` 合并。`StageStrategy` 是结构化 Protocol
（`stage` + `async run(inputs)`），`inputs` 的形状仍是平台的私有 `StageInputs`，所以整个 kind 标 alpha。
内置 profile id 五个，实现在批 3。
