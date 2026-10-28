---
code_file: backend/routes/_ownership.py
stub: false
last_verified: 2026-10-28
---

## 2026-10-28 — 第三个 surface：`require_owned_workspace_agent`（跨租户读事件）

`/api/agents/../files` 把 URL 段拼进 `{base}/{user_id}/{agent_id}` 读走了 246 个用户的文件。
修复把"URL 里的 agent_id → 目录"之间缺失的确权收口到本文件，而不是再造一份副本
（本文件存在的理由就是副本会漂移）。它与前两个 surface 的差异都是刻意的：

- **身份必须存在**（`resolve_current_user_id`，无则 401），没有 local-mode 放行——保护对象
  是文件系统，不是 bot 绑定；
- 先过 [[workspace_paths]] 的 `safe_id_segment`，`..` / `%2e%2e` / `a/b` 在任何 `Path` 之前就被拒；
- unknown 与 not_owner **统一 404**：面向磁盘的路由不能成为"别人的 agent_id 存在吗"的探针；
- 查库失败仍由 `_deny_reason` 抛 503，三个 surface 共享同一决策。
- 返回 user_id，路由用被校验过的同一身份拼路径。

调用方：[[files]]、[[attachments]]（backend 直接 import），以及插件侧的 skills 路由经
[[web]] seam（`sdk.web.require_workspace_agent_owner` → [[plugin_sdk_host]]）。

## Why it exists

Canonical agent-ownership verification for backend routes. The channel
routes (slack/lark/telegram/discord/wechat/narramessenger) and
home_assistant had each pasted their own `_verify_agent_ownership` /
`_require_agent_owner` — the copies drift apart, which is exactly the bug
`AgentRepository.resolve_owner` already fixed on the agent side. This is its
backend-route counterpart, built ON that seam. Same-shape copies NOT yet
absorbed (each with drifted semantics needing its own judgement, tracked as
follow-ups): `agents/artifacts.py`, `agents/llm_config.py`,
`agents/circuit_breaker.py`, `migrate.py` (that one checks existence even in
local mode — replacing it with `assert_owned` would NOT be equivalent).

## Model

Ownership = `agents.created_by` (via `AgentRepository.resolve_owner`). ONE
internal deny-reason decision (`_deny_reason` → unknown / not_owner /
None-allow, **db failure raises 503 right there**); the two public surfaces
only map reasons to their historical shapes — neither ever parses the
other's prose (review #1: the 404-vs-403 split used to hang off an
error-string substring):
- `check_owned(request, agent_id) -> str | None` — channel routes wrap the
  string in `{"success": False, "error": ...}`.
- `assert_owned(request, agent_id)` — HTTPException(404 unknown / 403
  non-owner); home_assistant-style calls it directly now (the pass-through
  wrapper is gone).

## Decisions / gotchas

- **SECURITY POSTURE (the load-bearing warning, review #5).** In local mode
  (no `request.state.user_id`) enforcement is SKIPPED — every route behind
  this helper is then effectively unauthenticated; any HTTP caller reaching
  the backend port can bind/unbind/test any bot. Do NOT hang sensitive
  operations off this helper assuming auth exists. All IM-channel routes
  mirror this contract; keep them in lockstep.
- **Infrastructure failure ≠ "not found" (review #4, round-2 #4).**
  `resolve_owner` distinguishes `None` (lookup failed) from `""` (unknown
  agent). The 503 for a failed lookup is raised inside `_deny_reason` so it
  reaches BOTH surfaces — `check_owned`'s callers wrap returned strings in
  200 payloads, and a string there would leave a db outage with zero 5xx to
  alarm on (incident lessons #3/#5).
- **Fail-closed on unknown owner** — an unknown agent denies (404), never
  leaks. An existing row with NULL/empty `created_by` also reads as unknown
  (404 where old copies said 403 — both deny; "an agent nobody owns" is
  closer to not-found than to somebody-else's; intentional).
- Channel files keep their historical local name via a module-level alias
  (`from ... import check_owned as _verify_agent_ownership`) — no per-file
  wrapper bodies to drift (review minor #6); no import cycle because this
  module never imports back into routes.
