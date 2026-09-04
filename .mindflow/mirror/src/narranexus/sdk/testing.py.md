---
code_file: src/narranexus/sdk/testing.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（批 2e）— `PluginTestHost`：围绕一个插件目录起最小宿主

上下文管理器：临时插件 home（`NARRANEXUS_PLUGIN_HOME` 进出恢复）→ link 安装 → 新 `Registries` 上跑
内核 `boot(role)` → 暴露注册表、`tables`、`hooks`、`activate()`（内存设置 store + EventBus + 服务定位器）、
`test_app()`（插件路由挂到无鉴权中间件的 FastAPI，测试直接打路由）。退出时 deactivate、卸合成包、关总线。
`plugin_id` 属性在插件被隔离时直接断言失败并给出原因——模板测试全部靠它。
