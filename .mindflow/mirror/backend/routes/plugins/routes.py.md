---
code_file: backend/routes/plugins/routes.py
last_verified: 2026-08-28
stub: false
---

# backend/routes/plugins/routes.py — HTTP surface for the local plugin installer

## 为什么存在

`backend.integrations.plugins.PluginService` (Phase 2 of the local slim-down:
Claude Code / Codex CLI moved out of the base pip install into
`~/.narranexus/plugins/`, see `plugin_paths.py`) is a pure Python facade with
no HTTP awareness. This file is the thin route layer the Settings → Plugins
panel talks to — list/install/uninstall — kept in its own grouped
`backend/routes/plugins/` package rather than folded into `providers.py`
because it owns a different resource (on-disk plugin installs, not per-user
DB rows) and needs its own long-lived singleton (see below).

## 上下游关系

- **被谁用**：`backend/main.py` imports `routes.router` and
  `app.include_router(plugins_router, tags=["Plugins"])`s it (the router
  carries its own `/api/plugins` prefix, matching the `admin/*` grouped-route
  pattern rather than `providers.py`'s external-prefix style). The frontend
  Settings → Plugins panel is the only HTTP client.
- **依赖谁**：`backend.integrations.plugins.service.PluginService` (the only
  import from that package — everything else, including `registry.py`'s
  `PLUGIN_SPECS` and the classified-error shapes, is reached only through the
  service's public methods). `xyz_agent_context.utils.deployment_mode.is_cloud_mode`
  is the same single source of truth `backend/routes/providers.py`'s `_is_cloud()`
  delegates to — this file calls it directly rather than re-deriving cloud
  detection.

## 设计决策

**Process-level `PluginService` singleton, not per-request.** `PluginService`
keeps its "is this plugin mid-install" `asyncio.Lock` + busy-set as instance
state (see `service.py`'s own docstring). A fresh instance per request would
give every request its own lock, defeating the re-entry guard against two
concurrent package-manager processes writing into the same target directory.
The module-level `_service = PluginService()` is the seam tests monkeypatch.

**Install's 404 is resolved BEFORE the streaming response is constructed.**
`PluginService.install` is an async generator; the `KeyError` it raises for
an unknown plugin id only fires once the generator body actually runs, i.e.
on the first `__anext__()`. If a `StreamingResponse` were constructed first
and the KeyError were left to surface during iteration, the 200 status line
would already be on the wire (Starlette commits headers on the first chunk)
and could never become a 404. So the route pulls the FIRST event off the
generator itself, inside a `try/except KeyError`, and only wraps the
(already-open) generator in `StreamingResponse` once that first pull
succeeds — re-injecting the already-consumed first event as the stream's
first line.

**Cloud gating is per-verb, not per-router.** `GET /api/plugins` (list) works
in every deployment mode — the frontend needs `cloud_managed` to decide
whether to render install/uninstall controls at all, so the read path can't
itself be cloud-gated. Only `install`/`uninstall` 403 in cloud mode (cloud
images pre-install both plugins into the base environment, so there's
nothing for a cloud user to install/uninstall on their own container).

**No `_get_user_id` — plugin install state is machine-local, not per-user.**
Unlike `providers.py`, no handler here reads `request.state.user_id`. The
global `auth_middleware` still gates every request (this router is not in
`backend.auth.AUTH_EXEMPT_PATHS`), so an unauthenticated caller still 401s
before reaching any handler — there's just no per-user data to scope.

## Gotcha / 边界情况

- **触发**：当你想给 install/uninstall 加请求体校验或速率限制时 → **症状**：容易忘记
  `PluginService`自己已经用 per-plugin lock 拒绝并发 install（见 `service.py`
  `install()` 的 `if lock.locked()` 分支，返回 `done:True, ok:False` 的
  ndjson 末帧而非抛异常）→ **根因**：并发保护已经在 service 层，路由层重复加锁
  会导致两套互不知情的互斥状态。
- **触发**：当你在 uninstall 成功后想直接返回 `PluginStatus` 而不重新
  `list_plugins()` 时 → **症状**：状态可能与 `installed=False` 不一致（安装/
  卸载都可能只清了部分 component）→ **根因**：`_status()` 是通过重新 `detect()`
  每个 component 现算的，不是缓存字段；success 之后必须重新查询才是真值。

## 新人易踩的坑

`is_cloud_mode()` 被直接 import 调用，而不是像 `providers.py` 那样再包一层
`_is_cloud()` helper——这里没有 staff 豁免这类额外语义要挂，直接调用单一真源
即可，多包一层反而是重新推导规则的坏味道（同样的教训见 `providers.py.md`
2026-07-18 条目：cloud policy 只能有一处）。

## 相关约束

- 架构分层表（API layer = `backend/routes/`）——本文件只 import
  `backend.integrations.plugins` 和 `xyz_agent_context.utils.deployment_mode`，
  不反向被 agent 侧代码 import；`backend/routes/plugins/` 是与
  `backend/routes/admin/` 同级的 grouped route 子包。
