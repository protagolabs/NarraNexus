---
code_file: backend/plugins_boot.py
last_verified: 2026-09-04
stub: false
---

## 2026-09-03（批 2b.5）— backend 进程的插件启动装配

把内核 `boot()` 接到本进程的东西上：`KERNEL_REGISTRIES`、`schema_registry.register_table`、`RegistryStore`
（每次现取，尊重被重定向的插件 home）、进程级 `HOST_BUS`/`HOST_SERVICES`、以及 `Activator`（上下文工厂：
`DbSettingsStore` + 该插件的 `backend.settings` schema + 同步 DB 客户端；crash sink 写 `record_crash`）。
`main.py` 在 `auto_migrate` 之前调 `boot_backend_plugins()`（插件表要先注册），之后 `fire_startup()` 激活
`onStartup` 插件并 `mark_healthy()`。注册表已冻结（测试里 lifespan 跑多次）时返回空报告不重复 boot。

## 2026-09-04 · services + host hooks (batch 3c.6)

`HOST_SERVICES` is `KERNEL_REGISTRIES.services` (no separate locator), so builtin services and user-plugin services share one namespace.
