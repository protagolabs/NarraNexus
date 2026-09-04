---
code_file: src/narranexus/kernel/plugins/registries.py
last_verified: 2026-09-04
stub: false
---

## 2026-09-04（批 3c.1）— `remove_owner` 跨全部注册表 + 钩子 block；`agent.capabilities.modules` kind 映射

## 2026-09-04（批 3a）— 七个阶段位、profiles、context_providers 的 kind 映射

## 2026-09-03（批 2e）— 每个 `Registries` 默认声明宿主钩子词表

`contracts.events.HOST_EVENTS`（参数取自 payload TypedDict）+ `contracts.agent.events.STAGE_HOOKS` 十四个
阶段钩子。宿主不必记得逐个声明，插件 `backend.hooks` 在任何进程都能对上（hello-world 实锤：忘了声明就整个
插件被隔离）。

## 2026-09-03（批 2a）— `SLOT_KINDS` 补十个平台/内容位

新位的注册表带各自 kind 的契约版本；`backend.hooks` 没有 `Registry`（走 `HookRegistry`），loader 特判。

## 2026-09-03（预审修订）— `SLOT_KINDS`/`_NORMALIZERS` 的框架位路径改为 `turn.pipeline.act.framework`

两张表是路径键，随扩展位树的路径重排一起改；对象身份与语义不变。

## 2026-09-03 — `Registries` 门面：每个扩展位一张注册表，进程内唯一实例

平台代码从这里取注册表（`registry_for(path)`），不再各处私建 dict——批 0 的出口判据
「registries 已被平台消费」就是 `loop/driver.FRAMEWORK_REGISTRY`、`providers/driver/registry.
DRIVER_REGISTRY`、`memory/spec.MEMORY_KIND_REGISTRY` 三者都 `is KERNEL_REGISTRIES.registry_for(...)`
（`tests/nx_kernel/kernel/test_loader.py` 钉住）。注册表按扩展位路径惰性创建，契约版本来自
`SLOT_KINDS`（路径→kind），键归一化来自 `_NORMALIZERS`（框架名大小写不敏感）——这两张小表是
「kind 特有知识」唯一允许出现在内核的地方，因为它们是纯数据。
`one` 与 `many` 位都用同一个 `Registry`：元数是绑定语义（换/追加），注册表只是「按名字存候选」。
`freeze()` 传播到已建与后建的注册表。`snapshot()` 给出 path→{name→owner} 的确定性视图，
loader 测试拿它和 approval golden 比对。测试自建 `Registries()` 得到干净实例。

## 2026-09-04 · ingress triggers (batch 3c.3)

`SLOT_KINDS["ingress.triggers"] = "trigger"`.

## 2026-09-04 · data-access providers (batch 3c.4)

`SLOT_KINDS["agent.capabilities.data_access"] = "data_access"`.

## 2026-09-04 · services + host hooks (batch 3c.6)

`Registries.services` — one `ServiceLocator` per process (builtins expose at import via `register_all`, user plugins through `PluginContext.services`); `remove_owner` also releases the owner's services.

## 2026-09-04 · channels as descriptors (batch 4a)

`SLOT_KINDS["ingress.channels"] = "channel"`.
