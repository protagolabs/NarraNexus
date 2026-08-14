---
code_file: backend/routes/admin/runtime.py
stub: false
last_verified: 2026-08-13
---

## 2026-08-13 — admin-secret 校验改用共享 helper

原内联的 `_require_admin_secret` 已抽到共享私有模块 [[_admin_secret.py]]
（`require_admin_secret`），与 migration / suspend 同用一份。**503（未配置）/
403（缺失或错误）状态码语义严格不变**——部署侧告警 watcher 依赖 503 == 「功能关闭」。
比较改为 `hmac.compare_digest`（常量时间）。本文件不再直接 import `settings`
（仅为测试经 `mod.settings` 覆盖 secret 而再导出一份 `# noqa: F401`；helper 读同一
settings 单例）。

## 2026-07-22 — added GET /api/admin/runtime/workers (Workers card liveness)

Second read-only L2 endpoint in this module. Reads the latest
`worker_supervisor` heartbeat row from `service_audit` (via
`ServiceAuditRepository.last_heartbeat`, falling back to the `started` row on a
cold boot) and returns a per-worker snapshot `{available, heartbeat_age_seconds,
workers:[{name,state,restart_count,last_error}]}`. Feeds the desktop System
page's single "Workers" card ([[ServiceCard.tsx]] / [[run_worker_supervisor.py]]):
the four merged workers share one process, so the process dot can read "running"
while a sub-worker crash-loops — this exposes each sub-worker's state +
cumulative restart_count. Same discipline as `/status`: every sub-step guarded,
never 500s (DB blip or absent supervisor → `available:false`). Auth:
`/api/admin/*` needs a JWT in cloud but is bypassed in desktop local mode, which
is exactly (and only) where the Workers card renders.

## Why it exists

`GET /api/admin/runtime/status` — 只读 L2 可观测端点,给 executor 调度/资源系统
一个"分钟级发现问题"的窗口(scheduling-resource 设计 §9)。

三段拼装,每段独立容错,**任何单段失败都不让端点 500**:
- `admission`: `get_admission_controller().snapshot()`(活跃用户/loop、各 cap、
  排队深度、free_mem vs 内存阀)。
- `executors`: 经 broker `GET /executors` 取活容器列表;无 `BROKER_URL` 或 broker
  不可达 → `[]`(handler 层 try/except 兜底,`_get_executor_list` 可抛)。
- `audit_counts`: `ExecutorAuditRepository.counts_since(近1h)`,看 OOM/cull/
  orphan-reap 速率。

注入接缝:`get_db_client`(db_factory)、`get_admission_controller` 都是模块级名,
测试可 monkeypatch。本轮未加鉴权(管理端点,部署侧应在网关/反代限制访问 —— 待办)。

## 2026-07-30 — X-Admin-Secret 自凭据（watcher 401 修复）

deploy 仓 alert watcher 是无 JWT 的机器客户端；端点照 migrate-identity 先例
改为 auth 中间件放行 + handler 内 `X-Admin-Secret`（`settings.admin_secret_key`）
校验。未配置 secret = 503 拒绝（配置缺失不是敞门）。历史：watcher 设计时
（2026-06-18）此端点无鉴权，后来全局 JWT 上线把它 401 掉，而 watcher 一直没
部署所以静默失联了一个多月。
