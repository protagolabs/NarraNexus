---
code_file: src/xyz_agent_context/agent_runtime/admission.py
stub: false
last_verified: 2026-08-19
---

## 2026-08-19 — `claim_idle_users` 接受跨进程 `is_busy` 否决

进程级单例这件事,对**准入**无害(每个进程管住自己那份配额),对**空闲记账**
致命:`_idle_since` 只反映本进程的 run,而 executor 容器是按 user 共享的。
详细事故经过见 [[executor_reaper.py]]。

签名变成 `claim_idle_users(ttl_seconds, is_busy=None)`,`BusyCheck =
Callable[[str], Awaitable[bool]]`。三条不能改的语义:

1. **否决在锁外跑**。它会做 I/O(reaper 那个查 DB),握着 `_cond` 等 I/O 会让
   所有 `acquire`/`release` 排在后面。
2. **锁外跑就要二次校验**。第二段重新拿锁时比对
   `self._idle_since.get(u) != ts`:否决在飞的时候用户可能已经重新活跃
   (`acquire` 弹掉了戳)或又释放了一次(写了新戳),这两种都不认领。
3. **被否决的用户保留原戳**。claim 是破坏性的,这是整个改动的要点——见
   [[executor_reaper.py]] 里"为什么否决必须在这里"。异常也算 busy:拿不到
   结论不构成回收许可(铁律 #14)。

`is_busy=None` 保持原语义,只在"本进程是唯一跑 agent 的进程"时才安全(测试、
单进程部署);生产装配一律注入。

## 2026-06-18 — snapshot() + queue-depth observability

新增只读 `snapshot() -> dict`(active_users/active_loops/queue_depth/各 cap/
free_mem_mb/per_user_loops/enabled),供 `GET /api/admin/runtime/status` 暴露
L2 监测态。`acquire` 用 try/finally 维护 `self._waiting` 计数(进 `wait_for`
前 +1、出后 -1),让排队深度可观测。**纯观测,不改准入行为。**

## Why it exists

Two-level concurrency admission gate. One user can drive many agents at
once (chat + scheduled jobs + message-bus interactions), so without a cap
the box OOMs. This bounds it — and does so **only by delaying the START**
of a run (queueing), never by interrupting a running loop (binding rule
#14).

## Model

Four knobs (env-tunable, prod-sizing defaults in cloud: 20/5/50/6144):
- `MAX_CONCURRENT_USERS` (global, distinct active users; default 20 in cloud)
- `MAX_LOOPS_PER_USER` (per-user simultaneous loops — main anti-starvation; default 5)
- `MAX_CONCURRENT_LOOPS` (global total loops — the real RAM ceiling; default 50)
- `MIN_FREE_MEM_MB` (dynamic guard — hold new loops when free RAM is low,
  catches subagent memory spikes the loop counts can't predict; default 6144)

A run is admitted only when ALL hold (`asyncio.Condition.wait_for`);
otherwise it queues. Released slot → `notify_all` re-checks waiters.

## Decisions / gotchas

- **Local/desktop = unlimited (no-op).** Defaults are None + mem-guard 0
  unless `get_deployment_mode()=="cloud"`, so `bash run.sh` / DMG behave
  exactly as before (binding rule #7). Env vars override either way.
- **State behind the controller instance (a seam)** so it can move to
  Redis when the orchestrator scales to >1 replica (binding rule #20).
  Today it's an in-process singleton (`get_admission_controller`).
- **Integration point = the client seam** (`InProcessAgentRuntimeClient`
  `run_and_collect` / `run_stream` wrap the run in `controller.slot(user_id)`).
  Covers all trigger paths (job/bus/lark/slack/telegram/chat). NOTE: the
  backend WS path drives `BackgroundRun.runtime.run()` directly and does
  NOT yet go through the gate — follow-up to wrap it too.
- Fairness is cap-based (per-user M bounds any one user); a strict
  round-robin out-queue is a future refinement.
- `_free_mem_mb` reads `/proc/meminfo`; returns +inf off-Linux so the
  guard never blocks on desktop.

## Idle bookkeeping (feeds the executor reaper)

The controller also tracks WHEN each user dropped to zero active loops
(`_idle_since`, stamped in `release`, cleared in `acquire`), using an
injected `clock` (default `time.monotonic`, swappable for deterministic
tests). `claim_idle_users(ttl)` atomically returns + un-tracks users idle
≥ ttl. This is the controller's ONLY outward knowledge of culling — it
stays ignorant of brokers/executors. The reaper
([[executor_reaper.py]]) is the coordinator that consumes this and calls
the broker to stop them. Single-responsibility: controller = state,
reaper = WHEN, broker_client = HOW.
