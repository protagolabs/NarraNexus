---
code_file: backend/plugins_host.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（批 2a.4）— backend 宿主消费 `backend.routes`

`mount_plugin_routes(app, registries)` 在 `main.py` 里于全部壳路由之后、SPA 兜底之前调用一次：插件遮蔽不了
壳路由，兜底吞不掉插件路由。非 `builtin.` owner 的前缀强制在 `/api/x/<id>` 之下，越界只记 `refused`
不挂载；工厂抛错同样隔离（一个坏插件不能拖垮宿主）。`auth="none"` 是插件拿到公开端点的唯一途径：
前缀写进 `backend.auth.PLUGIN_EXEMPT_PREFIXES`（通过模块属性访问，测试可整体替换集合）。
没有用户插件时零挂载，`routes.json` 快照不变。
