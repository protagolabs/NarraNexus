---
code_file: src/narranexus/kernel/plugins/slots.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — 扩展位树：插件定义的正式基础（spec §6，D25）

`Slot` = 路径 + 契约符号 + 元数（`one` 可替换 / `many` 可追加）+ 默认提供者 + owner + 稳定级别 +
`distribution_only`。树按点分路径组织，**复合位的提供者拥有其子位的定义**：所以
`build_kernel_slot_tree()` 只种根（`kernel.*`、`turn.pipeline`、`turn.act.framework`、`model.*`、
`agent.capabilities.memory_kinds`、`ui`、各域根），七个阶段子位 `turn.ingress…reflect` 由批 3 的
`builtin.turn` 声明，nexus_power 的内接缝由 `builtin.frameworks.nexus_power` 声明——放在这里
就会把定义放错 owner。
声明 fail-loud：重复路径 `RegistryConflict`，父位未声明 `UnknownEntry`（一个静默缺失的位会在很久
之后表现为「插件装了但什么都不做」）。`many` 位不允许 `default`（没有「单一默认」的概念，
默认由绑定层的 `=a,b` 表达）。`to_rows()` 是文档生成与工场页的稳定视图。
契约符号目前只是字符串（如 `narranexus.contracts.agent.pipeline:TurnPipeline`），其中一部分
指向批 1/3 才存在的模块——这是刻意的：树先于实现存在，`manifest.py` 校验 `provides` 的 key
只看路径存在，`loader` 才解析符号。
