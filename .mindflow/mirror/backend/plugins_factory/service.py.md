---
code_file: backend/plugins_factory/service.py
last_verified: 2026-09-04
stub: false
---

## 2026-09-04（批 3b）— manifest 校验改用 `slot_tree_with_builtins()`（含内置声明的位）

## 2026-09-03（批 2f.1）— 提案与审计

`proposals()`/`decide_proposal()`：用户在工场页批准/拒绝 Agent 的提案，批准即 `SelfExtensionService.apply_decision`
（enable + scope + 权限确认 / 安装 / 升级）；`record_error` 同时写审计 `ui_error` 行供 observe 窗口读。

## 2026-09-03（批 2d）— 列表行带 `frontend`/`activation_events`/`protected`

前端加载器只靠这一个端点就知道要登记哪些 gate、哪些事件激活、去哪取 bundle。

## 2026-09-03（批 2c）— `FactoryService`

把内核件拼成工场页需要的一个对象：`RegistryStore`（状态）、`Installer`（来源/依赖/落位）、`Index`
（搜索 + 黑名单）、`Bisect`、上次 `BootReport`（谁加载了/谁被隔离）、每插件有界错误环（前端 errorSink 上报）。
云端只读：所有变更先 `_guard_mutation` 抛 `CloudManaged`（路由答 403）。`asset_path` 只在插件
`frontend/dist` 下解析且拒绝穿越。

## 2026-09-04 · builtin.teams as a feature-level plugin (batch 3c.2)

`list()` also returns `builtins` (id/display_name/enabled/protected/provides/dependencies from the manifests + overrides). `set_builtin_enabled()` writes `builtin_overrides` and cascades a disable to dependants (`because: <id>`); protected builtins refuse; enabling pops the override.
