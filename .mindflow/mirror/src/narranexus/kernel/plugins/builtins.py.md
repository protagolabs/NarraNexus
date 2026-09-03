---
code_file: src/narranexus/kernel/plugins/builtins.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（预审修订）— 框架位路径改为 `turn.pipeline.act.framework`；缓存拆分

`builtin_manifests()` 零参 `lru_cache`，`build_builtin_manifests(tree)` 不缓存，避免用可变树做缓存键。

## 2026-09-03 — 内置插件清单（显式注册，唯一真源）

D4「内置即插件」的落点：五份 manifest 常量——三个框架（各一个插件，`nexus_power` 常驻、
`claude_code`/`codex_cli` 依赖 `on_demand`，吸收 D7 的安装表语义）、`builtin.providers`（九个 driver
的 `CONTRIBUTION`，`system` 给 `CONTRIBUTIONS` 在本地为空）、`builtin.memory_kinds`。
`provides` 指向遗留模块里的 `Contribution` 常量（`agent_framework:NEXUS_POWER`、
`drivers.netmind:CONTRIBUTION`、`memory.specs:CONTRIBUTIONS`），而不是指向类或工厂——命名规则
（框架名、`driver_type()`、`kind`）留在各自领域，内核不学任何 kind 的取名法。
选择显式清单而非目录扫描（参考文档 §E-24：确定性、可 grep、启动快）。`builtin_manifests()`
带 `lru_cache`，因为数据是常量；批 3 逐个抽取内置时，这里每插件一条。`hosts` 目前只按进程角色
粗分（框架只在 backend 装），`mcp`/`workers` 装 providers 与 memory kinds。
