---
code_file: src/xyz_agent_context/agent_runtime/admission.py
stub: false
last_verified: 2026-08-20
---
## 2026-08-20 — `restamp_idle` + `BusyCheck` 拆成两个 Protocol

**`restamp_idle(user_id)` —— 与 `claim_idle_users` 破坏性语义的配对。**
claim 会把 idle 戳拿走,所以"claim 了但最终没停"的调用方必须能把戳放回去,
否则就是本文档 2026-08-19 段反复强调的那种永久泄漏(主要在别的进程里跑的用户
在本进程永远等不到下一次 `release`)。唯一调用方是 [[executor_reaper.py]] 在
stop 前二次判活发现"又忙起来了"的那条 `continue`。

- **必须是 `setdefault` 而不是赋值**:覆盖会把一个更早的真实戳往后推,等于白送
  该用户一整个 TTL。
- **活跃用户不写戳**:`user_id in self._per_user` 时直接返回——正在跑的用户不是
  "空闲于此刻",给它打戳会让下一轮把它当候选。
- 戳被 claim 拿走后放回去的值是"此刻":它刚才确实在忙,所以再等一个完整 TTL 是
  诚实的,不是 bug。这条取舍在 [[executor_reaper.py]] 那侧也写了,两处口径一致。

**`BusyCheck` 从类型别名变成 `Protocol`,并拆成两个。**
`BusyCheck` 只有 `__call__`(裸 async 函数即可满足,测试与单进程部署都靠这个);
`AgingBusyCheck(BusyCheck, Protocol)` 才加 `pass_()`,给需要跨调用保存状态、
因而需要知道"一轮到此为止"的实现用(reaper 的 `_CullVeto` 用它 age 审计去重)。

拆成两个而不是"一个 Protocol + 一个可选成员":**Protocol 的成员在结构上是必填
的**,写 `# optional` 注释不改变类型含义——单个 Protocol 会把明确受支持的裸函数
形式排除在外。

**注意它买到了什么、没买到什么**:契约现在可表达、编辑器和手跑 pyright 能查,
但**不是 CI 门禁**——`pyrightconfig.json` 的 include 只有
`src/xyz_agent_context/module` 且 `typeCheckingMode: "off"`。所以真正防住"忘记
调 `pass_` → 状态永不老化 → 无界增长"的,不是类型,而是 `_CullVeto` 自己给
`_blocked_by` 加了硬上限(`_MAX_TRACKED`):忘记的代价降级成几行重复审计,不是内存。



## 2026-08-19 — `claim_idle_users` 接受跨进程 `is_busy` 否决

进程级单例这件事,对**准入**无害(每个进程管住自己那份配额),对**空闲记账**
致命:`_idle_since` 只反映本进程的 run,而 executor 容器是按 user 共享的。
详细事故经过见 [[executor_reaper.py]]。

签名变成 `claim_idle_users(ttl_seconds, is_busy=None)`。(`BusyCheck` 的形状
在 2026-08-20 段又变了一次,见下。)三条不能改的语义:

1. **否决在锁外跑**,并用 `Semaphore(_VETO_CONCURRENCY=8)` 限流。它会做 I/O
   (reaper 那个查 DB),握着 `_cond` 等 I/O 会让所有 `acquire`/`release` 排在
   后面;而候选数是调用方可控的(这一轮跨过 TTL 的全部用户),不限流的话一轮
   突发会和在线请求抢同一个连接池。
2. **锁外跑就要二次校验**。第二段重新拿锁时比对
   `self._idle_since.get(u) != ts`:否决在飞的时候用户可能已经重新活跃
   (`acquire` 弹掉了戳)或又释放了一次(写了新戳),这两种都不认领。
3. **被否决的用户保留原戳**。claim 是破坏性的,这是整个改动的要点——见
   [[executor_reaper.py]] 里"为什么否决必须在这里"。异常也算 busy:拿不到
   结论不构成回收许可(铁律 #14)。

整批否决还包在 `wait_for(_VETO_BATCH_TIMEOUT_S=60)` 里:`is_busy` 做 DB I/O,
连接池挂死会让 `gather` 永不返回 → `reap_once` 永不返回 → 整个 reaper 循环静默
停摆,没有异常、没有日志、done-callback 也不会响(事故教训 #4:L1 只能发现"任务
还在不在",发现不了"任务还醒不醒")。超时按"全部算忙"处理,即返回空 claim。
这是给探测本身的预算,不是给任何 agent 行为的上限,与铁律 #14 无关。

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
≥ ttl, and `restamp_idle(user)` puts a stamp back when a claim is not acted
on (2026-08-20 段). Those two are the controller's whole outward knowledge of
culling — it stays ignorant of brokers/executors. The reaper
([[executor_reaper.py]]) is the coordinator that consumes this and calls
the broker to stop them. Single-responsibility: controller = state,
reaper = WHEN, broker_client = HOW.
