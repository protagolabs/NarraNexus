---
code_file: backend/plugins_host.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（批 2b.4）— `LazyRouterApp`

按插件前缀 `app.mount` 一个 ASGI 小应用：首个请求才 `await activate()` 拿到 `RouterSpec`、建子 FastAPI 并
include；之后直通。认证中间件在主 app 上先跑，所以未登录请求根本不会触发激活。激活失败记住错误、
答 503（带插件 id），不在请求循环里反复重试。`mount_lazy_router` 强制前缀在 `/api/x/<id>` 之下。

## 2026-09-03（批 2a.4）— backend 宿主消费 `backend.routes`

`mount_plugin_routes(app, registries)` 在 `main.py` 里于全部壳路由之后、SPA 兜底之前调用一次：插件遮蔽不了
壳路由，兜底吞不掉插件路由。非 `builtin.` owner 的前缀强制在 `/api/x/<id>` 之下，越界只记 `refused`
不挂载；工厂抛错同样隔离（一个坏插件不能拖垮宿主）。`auth="none"` 是插件拿到公开端点的唯一途径：
前缀写进 `backend.auth.PLUGIN_EXEMPT_PREFIXES`（通过模块属性访问，测试可整体替换集合）。
没有用户插件时零挂载，`routes.json` 快照不变。
