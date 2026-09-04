---
code_file: src/narranexus/kernel/plugins/slots.py
last_verified: 2026-09-04
stub: false
---

## 2026-09-04（批 3a）— `turn.profiles`、`agent.capabilities.context_providers`；`turn.pipeline.act` 改 many

阶段位放的是**命名的策略**（profile 点名其一），所以 act 与六个由 `builtin.turn` 声明的兄弟位一样是 many。

## 2026-09-03（批 2a）— 平台服务与内容位

`backend.{routes,tables,workers,settings,hooks}`、`agent.capabilities.{tools,mcp_servers}`、`ui.themes`、
`content.{bundles,skills}` 十个 `many` 位进内核树（owner 内核，alpha）；`backend.hooks` 的契约符号是
`HookImplSpec`。

## 2026-09-03（预审修订）— 阶段位搬到 `turn.pipeline.*` 之下；命名空间自动补全

预审发现 `turn.act` 与 `turn.pipeline` 是兄弟，路径后代关系与「复合位提供者拥有子位」不一致，
嵌套规则在真实树上永远打不到。现在 `turn.pipeline.act` / `turn.pipeline.act.framework` 是
`turn.pipeline` 的后代（v3 spec §6.2 的示意树需同步改成这个写法）。`declare(create_namespaces=True)`
用 `namespace_slot` 自动补出缺失祖先（owner 同声明者），供插件声明自己的扩展点。

## 2026-09-03 — 扩展位树：插件定义的正式基础（spec §6，D25）

`Slot` = 路径 + 契约符号 + 元数（`one` 可替换 / `many` 可追加）+ 默认提供者 + owner + 稳定级别 +
`distribution_only`。树按点分路径组织，**复合位的提供者拥有其子位的定义**：所以
`build_kernel_slot_tree()` 只种根（`kernel.*`、`turn.pipeline`、`turn.pipeline.act.framework`、`model.*`、
`agent.capabilities.memory_kinds`、`ui`、各域根），七个阶段子位 `turn.ingress…reflect` 由批 3 的
`builtin.turn` 声明，nexus_power 的内接缝由 `builtin.frameworks.nexus_power` 声明——放在这里
就会把定义放错 owner。
声明 fail-loud：重复路径 `RegistryConflict`，父位未声明 `UnknownEntry`（一个静默缺失的位会在很久
之后表现为「插件装了但什么都不做」）。`many` 位不允许 `default`（没有「单一默认」的概念，
默认由绑定层的 `=a,b` 表达）。`to_rows()` 是文档生成与工场页的稳定视图。
契约符号是字符串（如 `narranexus.contracts.agent.pipeline:TurnPipeline`）；批 1 起每个符号都真实
存在（`Namespace`/`Kernel`/`services.*`/`ui.Shell`/`ModelResolver`/`TurnPipeline`/`ActStrategy`/
`agent_spec.CapabilitySet`），`test_every_kernel_slot_contract_symbol_resolves` 逐行 import 校验。
`manifest.py` 校验 `provides` 的 key 只看路径存在，`loader` 才解析提供者符号。
