---
code_file: src/narranexus/kernel/plugins/context.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（批 2b.3）— `PluginContext`：插件 `activate(ctx)` 能碰到的一切

按 spec §5.4 组装：`settings`（内核 `PluginSettings`）、`db`（`PluginDb`：非内置只能碰 `ext_<id>_` 表，只暴露
字典式动词，不给裸 SQL）、`events`（以插件 id 订阅，撤销挂到 `subscriptions`）、`registries`
（`ScopedRegistries`：只有 manifest `provides` 的位，其它直接报错而不是给个空注册表）、`services`、
`log = logger.bind(plugin=id)`、`api_version/require_api`。`dispose()` 展开 `DisposableStack`，deactivate 靠它
收尾。边界只传值：不把 app 对象或裸注册表交给插件。`build_context` 是宿主激活器的装配入口。
