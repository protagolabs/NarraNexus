---
code_file: src/narranexus/kernel/plugins/registries.py
last_verified: 2026-09-03
stub: false
---

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
