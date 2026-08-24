---
code_file: backend/routes/channels/narramessenger.py
stub: false
last_verified: 2026-08-21
---

## 2026-08-21 — prewarm 现在带着 stale 镜像替换判决（它会导致容器被拆）

`_do_prewarm` 多了两件事实，都不是重构：

1. **它开始读 `events` 表** —— 一次 DB round-trip，进到了一个此前完全不碰 DB 的
   fire-and-forget 背景任务里。这次读发生在**路由级 `broker_url() is None` 守卫
   之后**（本地/桌面直接 202 `skipped`，压根不起这个 task），所以两种运行模式没有
   互相加税（铁律 #7）。读失败不特殊处理：`live_run_elsewhere` 从不抛，判不出来
   一律答"忙" → 判决为 `False` → 只是这一轮不滚镜像。
2. **它现在授权 broker 销毁这个用户的容器**（`allow_stale_replace`）。

**为什么 prewarm 该传判决，而不是吃默认 `False`**：prewarm 不是 run、它整个存在的
理由就是把冷启动搬到用户等待之外。留默认的话，每次 executor 镜像重建后，该用户在
NarraMessenger 上的首次交互会变成：prewarm 把**旧镜像**的容器暖好（broker 因
`allow=False` 推迟替换）→ 紧接着同一轮 step 3 判决为 `True` → broker stop +
`_await_gone` + `docker run` + `wait_until_ready` **全额落在用户这一轮里**，prewarm
白跑。首轮延迟严格变差，且只在部署后出现一次，最难归因。

**残留风险，写下来而不是论证掉**：判决只覆盖 **recorded run**。同一个容器里可能有
活着的 office-watch 会话（`officecli watch` 跑在容器内部、端口由容器分配，见
[[proxy.py]]），判决看不见它 —— 从这里授权的替换会把那个会话一起拆掉，用户视角是
"文档同步/编辑突然不工作了"。

注意**不能**用"prewarm 自己没在容器上占东西"来论证安全：授权销毁的是**容器**，
要证的命题是"容器上没有任何人"，不是"这个调用方没占"。今天接受这个残留，是因为
空闲回收器**已经**在 20 分钟 idle TTL 上拆这种容器（office-watch 的
`ensure_executor` 也不刷新 admission 的 idle 戳），所以这里只是多了一个触发时刻，
不是新故障类别。正解：让 `watch/ensure` 落一条 lease 行（用户 + 过期时间），`live_run_elsewhere`
一起读 —— 在同一个口径里治，三个消费方自动都受保护，而不是每个调用方各自打补丁。
（细节另有本地笔记 `reference/self_notebook/todo/…`，**未入库**，不必依赖它。）

**镜像滚动是 best-effort**：只有真正走到 `_do_prewarm` 的那次 prewarm 会带判决。
ledger 里已有 `ready` 条目且容器健康时，路由直接 202 `already_warm`、压根不调
`ensure_executor`，于是那个跑着旧镜像的健康容器不会被滚，替换又落回用户那一轮。
窗口很窄（`_PREWARM_STATE` 是进程内的，backend 重启即清空；条目要维持 `ready` 还
得容器在 20 分钟 idle TTL 内活着），也**不应该**为它在 `already_warm` 那条路上插
一次 ensure —— 那条路存在的理由就是让响铃期间的重复 POST 不打 broker。

> 2026-08-10:`_verify_agent_ownership` 不再是本文件定义——模块级别名指向
> `backend/routes/_ownership.py::check_owned`(canonical;DB 故障走 503 而非 200)。

## 2026-08-11 — sandbox prewarm endpoints (F28 voice)

`POST /prewarm` + `GET /prewarm/status`: the NarraMessenger backend calls
these when a voice call starts ringing, so the owner's executor container is
warm before the call connects (cold start costs up to tens of seconds behind
the "connecting" UI). Contract field names are FROZEN — already published to
the partner.

- **Auth is the per-agent `bearer_token`**, NOT a user JWT: the caller is a
  machine with no session. Both paths sit in `AUTH_EXEMPT_PATHS` and
  self-credential in-handler via `hmac.compare_digest` (timing-safe), same
  pattern as `/api/admin/runtime/status`. Ordering: identifier misuse → 422,
  missing bearer → 401 (BEFORE any db work), then the row lookup (needed to
  obtain the expected token) → 404 unknown/disabled, mismatch → 403.
- **Executors are per-USER**: resolve `agent_id -> owner` through
  `AgentRepository.resolve_owner`, honoring its ""/None split (None = lookup
  failed → 503; "" = unknown agent → 404).
- `agent_profile_id` is the reserved secondary key — resolves only for rows
  bound after profileId persistence landed (2026-08-11); older bindings need
  a rebind. Its `max_length=64` matches the `nexus_profile_id` VARCHAR(64)
  column: longer values can never resolve, so they 422 instead of 404.
- **`_PREWARM_STATE` is an in-process ledger** (user_id →
  status/url/gen/task). Single-host by design today (binding rule #20): the
  durable seam is the broker itself — `ensure_executor` is idempotent, so a
  backend restart just re-reports `ready: false` and the next prewarm
  re-ensures. No broker configured (local/desktop) → 202 `"skipped"`, never
  an error.
- **In-flight dedup**: a POST that finds a live `"warming"` entry (task not
  done) answers 202 `"warming"` WITHOUT spawning another task — the partner
  may POST several times per ring, and piling ensure calls onto the broker
  helps nobody. Dead entries fall through, so retries never wedge.
- **Failure drops the entry** (no `"failed"` status): a failed warm pops
  the ledger entry entirely — a parked failure status carries no
  information the next POST could use (it re-warms regardless), and the
  absence of an entry already means "not ready" to the status probe.
- **Generation guard**: each warmer task carries a `gen` from
  `_PREWARM_GEN`; every ledger write inside `_do_prewarm` (ready mutates in
  place; failure and broker-vanished pop) fires only if the entry's gen is
  still its own, so a stale task can never clobber a newer entry. The
  in-place ready write matters — the ledger entry IS the task's strong
  reference (the event loop only keeps weak refs; the old `_PREWARM_TASKS`
  set is gone). The dedup check means the route only ever replaces an entry
  whose task is already done (live warming entries return early), so no
  running task loses its ref; even a hypothetically superseded task would
  just finish with its writes no-oping on the gen guard.
- The route stores the ledger entry BEFORE `create_task` and patches
  `entry["task"]` in with no await in between — the one ordering where
  neither the new task nor a concurrent request can observe a half-built
  entry (see the inline comment).
- The warmer is a fire-and-forget task that catches ALL its own exceptions
  (engineering lesson #2); prewarm failure must never block the call itself.
- Liveness probing is `broker_client.executor_healthy` (made public
  2026-08-11 — the route no longer restates its own copy). Both call sites
  (POST already-warm check and `/prewarm/status`) pass `timeout=1.0`: the
  caller is mid-ring; a wedged container must not cost the full 5s default.
- `/prewarm/status` query params carry the same `max_length` bounds as the
  POST body (`fastapi.Query`): 255 for the matrix id, 64 for the profile id.

## Why it exists

The frontend "paste the bind link" entry point for NarraMessenger:
`GET /api/narramessenger/credential`, `POST /bind`, `POST /unbind`. Mirrors
`backend/routes/channels/lark.py` (same `_verify_agent_ownership` local-vs-cloud pattern).

## Design decisions

- **All real work lives in `_narramessenger_service.do_bind` / `do_unbind`** —
  shared with the `narra_bind` MCP tool, so the chat path and the dashboard path
  bind identically. The route is a thin auth + validation wrapper.
- `/credential` returns the sanitised `get_public()` view (NO bearer token);
  `data` is null when unbound — which is what `IMChannelsSection.fetchConnected`
  keys on for the ✓/not-bound badge.
- Registered in `backend/main.py` under `/api/narramessenger`.
