---
code_file: src/narranexus/platform/agent_runtime/client.py
stub: false
last_verified: 2026-09-10
---
## 2026-09-10（GH #127 / B-05）— natural-end 不再无条件写 STATE_COMPLETED

`run_and_collect`/`run_stream` 里两处 `recorder.finalize(STATE_COMPLETED)`
——生成器**没抛异常**就直接判完成——改调新增的 `_finalize_natural_end()`：
读 `recorder.had_fatal_error`,是就转 `finalize(STATE_FAILED,
error_message=recorder.last_error_message or 兜底文案)`。

根因:一个致命 `ErrorMessage`(死 key、B-03 的截断重试也救不回来)会让
生成器**正常 return**,不是抛异常——异常分支(`except Exception`)一直都
正确写 STATE_FAILED,漏的正是这条"没异常但也没真正成功"的路径。修之前
events 行落地 `state=completed` + `error_message` 为空,Run-observation
侧栏显示一次绿色完成、内容却是空的,ops 只能从
`[AGENT-LOOP-RECOVERABLE]` 之类的应用日志里看出问题(GH #127)。

`had_fatal_error` 早就存在且被 `background_run.py` 的漏斗/熔断信号读过
(见该文件同日条目)——缺的只是"终态持久化也读它"这一步。

测试:`tests/agent_runtime/test_client_recording.py` 的
`test_run_and_collect_finalizes_failed_on_fatal_error_without_exception` +
`test_run_stream_finalizes_failed_on_fatal_error_without_exception`。

## 2026-08-07 — 把触发树交给 recorder

新增 `_inherited_root_run_id(extra_kwargs)`:从 `trigger_extra_data` 读出
trigger 声明的树,传给 `RunRecorder`。两个 recorder 创建点都改了。

`""` 与缺失都归一为 None —— 开启一棵树的 run(用户在房间里发言、job 到点)
传空值,绝不能被读成"一棵叫空字符串的树"。这个 seam 一旦两侧改名,每个被
引发的 run 都会静默变成孤儿而没有任何测试会红,所以单独钉了测试。

## 2026-07-31 (二次) — run_stream 补 finally 兜底网（review Minor #3）

宿主 task 在生成器挂起点被 cancel 抛 CancelledError（BaseException，
不进 except Exception）——原来这条路径靠周期清扫兜（~90s+60s）。补与
run_and_collect 对称的 finally `_spawn_finalize(failed)`；用
`finalize_deferred` 旗标防它与 GeneratorExit 分支的 deferred finalize
双重竞速（spawn 是 task，finally 执行瞬间 recorder.state 还没翻终态，
仅看 state 会双开两个不同终态的 finalize）。

## 2026-07-31 — 咽喉点挂载 RunRecorder：所有 trigger run 可观察

`run_and_collect` / `run_stream` 都在此挂 [[run_recorder]]：装饰器
`_RecordedRuntime` 包住 runtime（yield 原 typed message，旁路喂
recorder 规范化 dict —— collect_run 零改动），完成/取消/异常在 client
层映射 finalize 终态；宿主 task 被掐（部署重启）走 `_spawn_finalize`
兜底（GeneratorExit 展开中不可 await，规避 lesson #2 的静默 GC）。
recorder 建不出来（杀开关 NARRANEXUS_RUN_RECORDING_DISABLED / DB 不可
得）时 run 照常裸跑 —— 观察者绝不挡 run。lark_trigger 的遗留直调
collect_run 也已并入本 seam（顺带补上 admission 闸门）。未来
HttpAgentRuntimeClient 落地时 recorder 跟传输走（server 侧），trigger
仍然零改动。测试：tests/agent_runtime/test_client_recording.py。

## 2026-07-02 — `silent=True` opt-in flows through the extra_kwargs seam

No signature change. Both `run_and_collect` and `run_stream` already
forward `**extra_kwargs` verbatim to `AgentRuntime.run` (via
`collect_run` for the collect case, direct for streaming), so newer
opt-ins like `silent=True` (skip step_3; memory-only writes; see
[[agent_runtime.py]] silent-mode note) reach the runtime without a
protocol bump. The module docstring now names this behaviour
explicitly so triggers know they can pass `silent=True` as a plain
kwarg. Locked by `tests/agent_runtime/test_silent_mode.py` — the
kwarg propagation test would break loudly if any future filter
in the client silently dropped it.

## Why it exists

`AgentRuntimeClient` — the single seam every trigger uses to run an agent
instead of constructing `AgentRuntime` directly. Goal: route all
in-process agent execution through one interface so (a) the transport can
later become HTTP to a remote agent-runtime service (control-plane /
data-plane split, binding rule #20) and (b) cross-cutting policy
(concurrency admission) lives in one place.

- `AgentRuntimeClient` (Protocol): `run_and_collect` (drive to completion →
  `RunCollection`) + `run_stream` (yield events live).
- `InProcessAgentRuntimeClient`: behaviour-identical to the old
  `collect_run(AgentRuntime(), …)` / `AgentRuntime().run(…)` calls, now
  wrapped by the two-level admission gate (`admission.get_admission_controller().slot(user_id)`)
  — no-op locally, enforced in cloud (rule #14: queues start, never kills).
- `get_agent_runtime_client()` factory — InProcess today; HTTP transport
  to the extracted agent-runtime service is the future swap (only this
  function changes, no trigger does).

## Gotchas

- `run_stream` is an **async generator function** (so the admission slot
  is held for the stream's lifetime via `async with`). Callers still just
  `async for ... in client.run_stream(...)` — identical usage.
- Lazy imports inside the methods avoid the channel/__init__ ↔ AgentRuntime
  circular import; safe to import the client at any trigger's top level.
- Migrated callers: `channel_trigger_base` (lark/slack/telegram),
  `job_trigger`, `message_bus_trigger`, `chat_trigger` (collect + A2A SSE).
  The backend WS path uses `BackgroundRun` directly, not this client (so
  it bypasses the admission gate for now — see admission.py.md).
