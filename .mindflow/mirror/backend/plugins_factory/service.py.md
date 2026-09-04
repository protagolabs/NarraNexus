---
code_file: backend/plugins_factory/service.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（批 2c）— `FactoryService`

把内核件拼成工场页需要的一个对象：`RegistryStore`（状态）、`Installer`（来源/依赖/落位）、`Index`
（搜索 + 黑名单）、`Bisect`、上次 `BootReport`（谁加载了/谁被隔离）、每插件有界错误环（前端 errorSink 上报）。
云端只读：所有变更先 `_guard_mutation` 抛 `CloudManaged`（路由答 403）。`asset_path` 只在插件
`frontend/dist` 下解析且拒绝穿越。
