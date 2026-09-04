---
code_file: backend/plugins_host.py
last_verified: 2026-09-04
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

## 2026-09-04 · builtin.teams as a feature-level plugin (batch 3c.2)

`register_builtins_for_import()` runs at `backend.main` import so builtin contributions (notably `builtin.teams`' router) exist before `mount_plugin_routes`; it drops `builtin_overrides`-disabled owners first, and the lifespan `boot` repeats the load idempotently. `start_backend_workers()/stop_backend_workers()` are the only consumer of `backend.workers` specs with `host="backend"`: they run inside the API process, one task per spec, failures isolated per plugin, stop awaited with a bounded timeout. Nothing in this file knows what teams is.

## 2026-09-04 · on-demand builtin dependencies (batch 3d.3)

`register_builtins_for_import` applies the same on-demand dependency probe as the boot, so a deps-missing builtin's routes/workers are not mounted at import either.
