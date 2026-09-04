---
code_file: src/narranexus/kernel/plugins/builtins.py
last_verified: 2026-09-04
stub: false
---

## 2026-09-04（批 3c.1）— 每个内置模块一份 manifest（17 份）

`builtin.chat/awareness/basic_info/social_network/job/skills/message_bus/common_tools/general_memory/home_assistant`
+ 六个 `builtin.channels.*`，各 provides `agent.capabilities.modules: PLUGIN_<ID>`；`builtin.nexus_plugins_module`
补 provides。`test_disable_builtin_degrades_cleanly` 逐个禁用验证。

## 2026-09-04（批 3b）— `slot_tree_with_builtins()`

用户插件的校验树 = 内核树 + 每个内置 manifest `declares` 的位（如 `builtin.turn` 的六个阶段位）；否则第三方
Recall 策略永远装不上（hello-world 实锤）。installer/discover/publish-check/自我扩展 validate/工场都改用它。
装饰器教训再犯一次：在 `@lru_cache` 与 def 之间插函数会把缓存装到新函数上。

## 2026-09-04（批 3a）— `builtin.turn`

提供 `turn.pipeline`（编排器）与七个阶段的默认策略、五个 profile；`declares` 六个阶段子位（act 已在内核树）。
它是「复合位提供者拥有子位」规则的第一个真实用户。

## 2026-09-03（批 2f.1）— `builtin.nexus_plugins_module`（protected，无 provides）

模块本身是插件；`protected: true` 让工场与自我扩展工具都不能改它。

## 2026-09-03（批 1）— 新增 `builtin.llm_clients`

指向 `agent_framework.llm.helper_sdk:CONTRIBUTIONS`（anthropic/openai/cli 三个协议客户端），宿主
backend/mcp/workers。

## 2026-09-03（预审修订）— 框架位路径改为 `turn.pipeline.act.framework`；缓存拆分

`builtin_manifests()` 零参 `lru_cache`，`build_builtin_manifests(tree)` 不缓存，避免用可变树做缓存键。

## 2026-09-03 — 内置插件清单（显式注册，唯一真源）

D4「内置即插件」的落点：六份 manifest 常量（批 1 加 `builtin.llm_clients`）——三个框架（各一个插件，`nexus_power` 常驻、
`claude_code`/`codex_cli` 依赖 `on_demand`，吸收 D7 的安装表语义）、`builtin.providers`（九个 driver
的 `CONTRIBUTION`，`system` 给 `CONTRIBUTIONS` 在本地为空）、`builtin.memory_kinds`。
`provides` 指向遗留模块里的 `Contribution` 常量（`agent_framework:NEXUS_POWER`、
`drivers.netmind:CONTRIBUTION`、`memory.specs:CONTRIBUTIONS`），而不是指向类或工厂——命名规则
（框架名、`driver_type()`、`kind`）留在各自领域，内核不学任何 kind 的取名法。
选择显式清单而非目录扫描（参考文档 §E-24：确定性、可 grep、启动快）。`builtin_manifests()`
带 `lru_cache`，因为数据是常量；批 3 逐个抽取内置时，这里每插件一条。`hosts` 目前只按进程角色
粗分（框架只在 backend 装），`mcp`/`workers` 装 providers 与 memory kinds。

## 2026-09-04 · builtin.teams as a feature-level plugin (batch 3c.2)

First feature-level builtin: `builtin.teams` (hosts backend; provides `backend.routes` + `backend.workers`; depends on message_bus + chat so disabling either cascades). It is the template for extracting the remaining features — routes/worker arrive through contributions, the platform keeps no direct reference.

## 2026-09-04 · ingress triggers (batch 3c.3)

Six channel builtins, builtin.job and builtin.chat now also provide `ingress.triggers` (`module.contributions:TRIGGERS_*`); builtin.chat provides `backend.hooks` (`chat_module.plugin_hooks:HOOKS`).

## 2026-09-04 · data-access providers (batch 3c.4)

awareness/social_network/basic_info/job/chat also provide `agent.capabilities.data_access` and their `backend.routes` twins (`backend/routes/agents/{awareness,profile,social_network,narrative,jobs,chat_history}:ROUTES`).

## 2026-09-04 · plugin-owned router (batch 3c.5)

Channel builtins provide `backend.routes.channels.<ch>:ROUTES`; builtin.job adds `backend.routes.jobs:ROUTES` + `backend.routes.dashboard.jobs:ROUTES`; builtin.skills `backend.routes.skills:ROUTES`; builtin.home_assistant `backend.routes.home_assistant:ROUTES`.
