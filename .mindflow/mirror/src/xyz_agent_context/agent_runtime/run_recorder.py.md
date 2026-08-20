---
code_file: src/xyz_agent_context/agent_runtime/run_recorder.py
last_verified: 2026-08-19
stub: false
---
## 2026-08-19 — `user_has_live_run`:判活口径升到"用户"这一层

新增 `first_live_run_id(db, user_id)` —— 把 `run_is_live` 的答案聚到用户上,
成为**跨进程**"这个用户忙不忙"的唯一事实源。第一个消费者是
[[executor_reaper.py]]:executor 容器按 user 共享,但准入账本是进程级的,
backend 看不见 workers 里的 run,于是把在跑的容器当空闲停了(2026-07-31 事故)。

返回 **run id** 而不是 bool,是为了让否决方能说出"被谁拦住的"(reaper 的审计
行按 run 去重就靠它),同时避免任何人在别处再写一份 running+心跳 查询。查询走
`fields=[event_id,last_event_at,started_at]` 投影:events 行有好几列 MEDIUMTEXT,
判活只要这三列;`started_at` 不能省——首拍未落时 `run_is_live` 回退读它。

第二个消费者是 [[broker_client.py]]:broker 的 stale 镜像懒替换是同一个根因长
出来的第二个杀手,判决同样在编排侧算,靠 `exclude_run_id` 排除**提问者自己那条
run**(ensure 发生在 step 3,那时自己的 events 行已经是 running,不排除的话判决
恒为"忙",镜像永远滚不动)。

放在这里而不是 reaper 里,是因为本文件已经是判活口径的 SSOT ——
`run_is_live` 的注释写着"ONE answer",再在别处手搓一次 running+心跳 的查询
就是第二个口径。broker 侧(另一仓)的懒替换护栏也要查同一个口径。

**只留一个入口,而且它会抛**。中途曾经有过一个吞异常的薄封装
`user_has_live_run`,评审第二轮指出它已经零生产调用者,却被模块头指成"唯一
真相源"——同一个问题三个入口、三种失败语义,而文档指向最没用的那个。已删除。
现在 `first_live_run_id` 在 DB 读不出来时**抛**:那个歧义属于调用方,而做破坏性
动作的调用方绝不该继承别人的猜测。所有调用方都把它解成"忙",统一收在
[[executor_reaper.py]] 的 `live_run_elsewhere` 里。

方向上与 `run_is_live` 相反且必须相反(后者读不出时间戳时 fail-open 当"活着"),
两者其实是同一个原则——**不确定就别动它**:漏收一轮只多留一个闲置容器,误收会
打断正在干活的 agent(铁律 #14)。

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
