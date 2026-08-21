---
code_file: src/xyz_agent_context/agent_runtime/run_recorder.py
last_verified: 2026-08-21
stub: false
---

## 2026-08-21 — `first_live_run_id`：跨进程"这个用户忙不忙"的唯一口径

`run_is_live` 是**单条 run** 的判活口径；本次把同一口径抬到**用户**这一层。
动机是 2026-07-31 prod 事故：任何"据此销毁容器"的动作（[[executor_reaper.py]]
的空闲回收）都不能用进程内记账回答"这个用户还在不在跑" —— 编排是 backend +
workers 两个进程，各自只看得见自己那一半。`events` 表是它们唯一的交汇点
（事故教训 #5：DB 痕迹比日志 grep 可靠，"该在的行不在"本身就是证据）。

三个决定：
- **返回 run id 而不是 bool**：调用方要能说出"是谁挡住了我"（reaper 的审计
  行），否则别处会再写一遍 running+心跳的查询。
- **`exclude_run_id`**：提问者自己的 events 行在问的时候已经是 running，不
  排除的话判决恒为"忙"。
- **失败就抛，不吞**：这是唯一入口。包一层"出错返回 None"的便利函数等于给
  同一个问题定义了第二套 fail-safe 语义，而破坏性调用方恰恰是最不能继承别人
  猜测的那批。它们各自解决歧义，且**全部**解决成"忙"。

投影查询（只取 `event_id / last_event_at / started_at`）：events 行带着好几个
MEDIUMTEXT 列。`started_at` 必须留 —— 第一次心跳落地前 `run_is_live` 靠它兜底，
丢了会把刚起跑的 run 读成死的。
## 2026-08-10 — retain normalized action reason

Fatal capture retains `action_reason` beside error type/message so analytics
can classify quota/configuration/infrastructure/runtime without parsing copy.

## 2026-08-07 — root_run_id 的铸造:根 run 给自己盖章

`RunRecorder` 增加 `inherited_root_run_id`,在 `_bind_run_id` 里与 running
翻转**同一条 UPDATE** 写入 `root_run_id = inherited or run_id`。

- **放在 recorder 而不是 Event/create_event**:这是 run 控制事实(和 state /
  心跳 / cancel_requested_at 同族),不是叙事事实;而且 recorder 本来就在
  late-bind 时写这一行,零额外写。
- **与 running 翻转同一条 UPDATE**:任何 run 一旦可见,它所属的树就是完整
  的。分两次写会留下一个窗口,期间级联查询选不到这一行——停止静默漏掉一条
  分支,而且没有任何报错。
- 观察者纪律不变:写自己的记录字段与"绝不影响被观察的 run"不冲突(取消的
  执行者是独立的 [[cancel_watcher]],不在 recorder 里)。

## 2026-08-07 — sweep 尊重取消旗标:停止中的 run 落 cancelled 不落 failed

`sweep_stale_runs` 原来无条件把心跳停跳的 running 行翻 `failed`。加入
[[cancel_watcher]] 后这是个**间歇性错误终态**:用户点了停止,run 在优雅
退出前心跳恰好断了(或 watcher 所在进程被掐),这一行就被记成"故障"。

需求验收明写「停止不被记为失败、不触发失败重试与失败告警」,而原来的
行为让这条验收取决于一场竞速 —— run 自己的 finalize 有没有赶在心跳变
stale 之前。竞速导致的绿是最坏的绿:**重跑一次就过了**。

现在按 `cancel_requested_at` 分流:非空 → `cancelled` 且**不写
error_message**(cancelled 行上盖错误会让下游告警把用户的主动动作当成
事故);其余照旧 `failed` + "run lost"。

判活逻辑(`run_is_live`)一个字没动 —— 变的只是"死了之后记成什么"。

# run_recorder.py — run 可观察性的持久化半身（唯一事实源）

## 为什么存在

2026-07-31 之前，「run 可观察」是聊天 WS 的私有功能：只有 BackgroundRun
驱动的 run（WS 聊天 / openai-compat）会逐条写 `event_stream`、维护
`events` 行状态机和心跳；所有 trigger run（lark / team bus / job / A2A）
是黑箱。Owner 拍板把可观察性升级为**平台属性**：任何 run 留下同一份
live trace，任何读侧（聊天重连、team roster、未来 dashboard）走同一条
路观察。本文件就是那份契约的唯一事实源。

## 内容

- **run 状态机**：`STATE_RUNNING/COMPLETED/CANCELLED/FAILED` +
  `HEARTBEAT_INTERVAL_S`（30s）+ `RUN_STALE_AFTER_S`（3 拍）。从
  background_run.py 移入 —— 状态机属于持久化层，不属于 WS 传输层。
- **`run_is_live`**：读侧唯一判活口径（agents 列表、观察端点、清扫
  共用）。只读，绝不据此停 run（铁律 #14）。
- **`sweep_stale_runs`**：把心跳停跳的 running 行翻 failed。**按心跳
  判，不按进程判** —— run 可能活在别的容器里，「本进程不认识」证明
  不了死。backend lifespan 启动时 + 周期性调用。
- **事件规范化**：`normalise_event` / `event_to_wire` /
  `try_extract_event_id`（从 background_run 移入并去下划线公开）。
- **`RunRecorder`**：消费一个 run 的规范化事件流，写 `event_stream`
  行（組合 B 粒度：thinking 整段一行、tool_call/tool_output/
  text_delta/progress 各一行）+ `events` 行生命周期字段 + 心跳 task。
  `record()` 首个 Step-0 progress 帧 late-bind run_id；`finalize()`
  幂等收尾。两个可选回调：`on_run_id`（传输层注册）、
  `on_thinking_buffer`（Broadcaster 的段内快照镜像）。

## 设计决策

- **观察者纪律**：每个 DB 写独立 try/except —— 记录失败绝不弄坏被
  记录的 run。progress 帧也持久化（观察者要能回放 0~3 步流水线）。
- **杀开关** `NARRANEXUS_RUN_RECORDING_DISABLED` 只管 trigger 挂载面
  （client.py）；BackgroundRun 的持久化早于此开关存在，聊天重连依赖
  它，不受影响。
- seq 每 run 单 writer（per-run 单 recorder 实例），单调无竞争 ——
  观察端点的 tail-follow 依赖单调性。
- `finalize(completed)` 只在 `events.final_output` 为空时回填
  （step_4 的写入是权威）。fatal-completed run（死 key 自然结束）
  **不**写 error_message —— completed 行上盖错误会显示假故障，
  错误已作为 error stream 行存在。

## 上下游

被 `background_run.py`（组合）与 `agent_runtime/client.py`（trigger
挂载）消费；读侧是 `backend/routes/websocket.py` 的观察端点与
`backend/routes/auth.py` 的 active_run 富集；`backend/main.py`
lifespan 调 `sweep_stale_runs`。

测试：tests/agent_runtime/test_run_recorder.py。

## 2026-08-20 — classify_event 转公有(#334 minor)

两个模块消费即非私有:去下划线并入 __all__;background_run 同步改。
