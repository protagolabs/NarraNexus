---
code_file: plugins/builtin.channels.narramessenger/src/narranexus_plugins/narramessenger_module/voice/_voice_control.py
stub: false
last_verified: 2026-11-19
---

## Why it exists

Narra 语音契约 v15 控制面的**接线层**：`VoiceControlMixin`，混入 [[matrix_trigger.py]] 的 `MatrixTrigger`。
它收四种控制事件、在跑模型之前认领语音轮、发 `ack.v2`，并自带限速与审计。纯逻辑（事件解析、ack 形状、通话
registry、限速器）在 [[_voice_call.py]]，没有 I/O；这里是围着它的 I/O。

2026-11-19 从 `matrix_trigger.py` 原样搬出（零行为变更）。当时那个文件已超过 4000 行，而控制面是一个对外只有
四处接缝的子系统，后续「打断正在跑的轮次」、`progress.v2`、`dm_report` 都会继续往这里加。

**为什么是 mixin 而不是持有 trigger 引用的协作对象**：这里每个方法都在读 trigger 自己的状态和门——dedup store、
审计写入、`_authorize_event`、`_resolve_room_shape`、流式 kill switch。做成协作对象只是把 `self.` 换成
`self._trigger.`，依赖一条没少，还多一层间接。类型检查视角下 mixin 继承 `ChannelTriggerBase`（`_Base`，仅 `TYPE_CHECKING` 时成立，运行时是 `object`，MRO 不变），
所以基类成员（`_audit`、`_dedup_store`）走真实声明。只有 `MatrixTrigger` 自己定义的四个成员用类体里的存根声明
（`STREAMING_ENABLED`、`_authorize_event`、`_resolve_room_shape`、`_service_sender_mxid`）。存根要尽量少：
mixin 排在 MRO 前面，存根会在类型检查下盖住它后面的真实声明，而项目的 pyright 只开了「属性是否存在」这一类检查，
被存根盖住的名字等于在整个 `MatrixTrigger` 上都不再被检查，只能靠测试兜。
mixin 不反向 import `matrix_trigger`（那会成环）：appservice 机器人的 MXID 用 trigger 的方法
`_service_sender_mxid(credential)` 取。

trigger 侧的四处接缝见 `matrix_trigger.py.md` 的「2026-11-19（三）」条目。

## Design decisions

### 1. /sync 循环（trigger 调用） → `_dispatch_voice_control_event`

timeline 里的四种控制事件（nio 解析成 `UnknownEvent`）在 `_wrap_event` 之前被截下，**不 yield 给基类管线**
（它们不是提示）。sync 循环本身不做任何 I/O：每个事件交给一个 handler 任务。我们自己发的 ack 也会从 /sync 回来，
但类型不在四种之内，落到 `_wrap_event` 的未知类型分支被丢弃。

控制事件绕过了基类的 ingress breaker 和有界 worker 池，所以自带三道闸：

- **每个 agent 的在途 handler 上限**（`VOICE_CONTROL_MAX_HANDLERS_PER_AGENT`，64）。按 agent 计而不是全进程共用，
  否则一个租户的事件能把另一个租户的 `turn.cancelled` 挤掉。handler 只做有界的事，64 远高于几通并发电话在一批
  sync 里能带来的量；超限的事件丢弃，审计每个 agent 每分钟只写一行（在另一个任务里写，sync 循环不做 I/O）。
  名额在任务的 **done-callback** 里归还，不在协程自己的 `finally` 里：任务在第一步之前被 cancel 时协程的
  `finally` 根本不会跑，名额会泄漏，反复 stop/start 之后这个 agent 的控制事件会被永久判为超限。
- **房间内所有控制事件的限速**、**未知 session 准入的限速**（见 `_voice_call.py.md` 的 `VoiceRateLimiter`）。
  **门的顺序**：畸形、非来电者、过期这三道廉价的拒绝排在房间限速**之前**。注定不会被受理的事件不能花掉正在
  进行的通话的预算——最现实的场景不是攻击而是断连重连：积压的控制事件一次灌回来（全是过期的），如果先过限速，
  120 条额度被它们用完，排在回灌尾部的新通话 `call.started` 就被判限速丢掉，那通电话整通退回 v14。
  这三道的审计量不需要靠限速来兜，见下一条。
- **丢弃审计的预算**：每个 (agent, room) 每分钟最多写 10 行 `voice_control_dropped`，超出的只打 debug 日志。
  所有丢弃都经 `_audit_voice_control_drop` 这一个出口，所以这条预算对每道门都成立，**不依赖门的先后顺序**——
  这正是上面三道门可以放在限速前面的原因。审计表是排障最依赖的那张表，被噪声灌满等于把这次新加的可观测性
  自己废掉。例外：本身已经是「每窗口一条」的原因（`room_rate_limited` / `new_session_rate_limited` /
  `handler_cap` / `live_voice_disabled`）不占这个预算——洪泛会先把预算用完，而这几行恰恰是用来说明「这里在被洪泛」的。
  写进行里的 `kind`（畸形事件的类型是发送者随手写的）截到 80；`sender_id` 截到 128，即
  `channel_trigger_audit.sender_id` 在 MySQL 上的列宽（`voice_control_handled` 同样截）：超宽的值在 MySQL 上会让整行
  插入失败、审计丢失，而 SQLite 的 TEXT 列永远测不出来。
- **kill switch 在 dispatch 处直接短路**：不占 handler 名额、不为每个事件起任务，审计每个 agent 每分钟一行。
  运维是在系统扛不住的时候才翻这个开关，不能在那一刻把每个合法控制事件都变成一次写库。

**handler 只做有界的事（门、registry、墓碑），ack 另起任务。** 这是有意拆开的：ack 是对 homeserver 的 HTTP，
慢起来能挂很久；如果和取消处理绑在同一个任务里，homeserver 一慢，handler 名额被 ack 占满，后面的
`turn.cancelled` 进不来，墓碑和取消记录都不执行。ack 的发送超时单独设为 10 秒（默认发送预算是 60 秒，
加一次重试能让任务活两分钟，而 ack 只有新鲜时才有用）。

所有语音任务（handler + ack）进 `_voice_control_tasks` 并挂 done-callback（工程教训 #2：没有裸的 fire-and-forget）。
`stop()` 置 `_voice_stopping` 并 cancel 全部；之后 sync 循环 / worker 收尾期间再想起的任务一律不启动。
`start()` 把标志复位并清空 handler 计数和在途准入表，同一实例 stop 后再 start 仍然处理控制事件。

### 2. `_handle_voice_control_event` 的放行条件

全部成立才处理并 ack；任何一条不成立都是**丢弃且不 ack**（按此顺序判）：

- payload 合规（含 id 不超过 128 字符）；
- sender 不是自己，也不是 appservice 机器人；
- 事件不超过 5 分钟（冷启动 / 重连补回来的旧事件对应的通话早结束了）。比的是 homeserver 的 `origin_server_ts` 和本机
  时钟，所以丢弃的审计行带 `age_ms`：负数或大得离谱的值一眼就是时钟偏移，而不是真的旧事件；
- 房间没超过控制事件限速；
- `STREAMING_ENABLED` 为真（kill switch 下语音轮按文字轮跑、回复不带绑定，ack 了 Narra 就会要求绑定、回复全丢）；
- session 是本房间**退役过**的旧通话 → 丢弃（旧通话的晚到事件不能把当前通话挤掉）；
- session 未知 → 过准入：限速 + 和语音轮同样的两道门——权威的 1:1 房间（路由为 dm **且**有效成员恰好 2）
  + Narra `authorize-event`（`mentioned=False`）。同一个新 session 同时到达的几个事件**共用一次准入**
  （`_admit_voice_session_once`，按 agent + session + 房间 + 发送者）：重连补发时一通电话的 started / hint / cancel
  一起到，各自准入会各打一次 `authorize-event`、各占一个名额，把同房间下一通电话的 `call.started` 挤出限额。
  领头的那次准入如果崩了或被 cancel，等待者拿到的是 `admission_failed`（丢弃、不 ack、不进 registry）——
  **绝不能**把领头者的失败当成放行。
  未知 session 的 `call.ended` 直接忽略；
- 无论已知还是刚学到，最后都拿 registry 里的条目核对房间和来电者。

**审计**：处理成功写 `voice_control_handled`；丢弃写 `voice_control_dropped` 并带原因，受上面的每房间预算约束。
另有两类不逐条写：限速类的拒绝只记每个窗口的第一条（一场洪泛一行，而不是每个事件一行）；未知 session 的
`call.ended`（什么都没发生）。ack 发送失败另写 `transport_send_failed`；墓碑或认领时 dedup 落库失败（fail-open）
写 `dedup_fail_open`（`context` 区分 `voice_turn_tombstone` / `voice_turn_claim`）。

**为什么「不 ack」是安全方向**：Narra 2026-11-18 起的行为是，一通电话只要没 ack 过 `call.started`，回复就退回
v14 的「房间 + agent + 时间」匹配。所以任何疑点都选择不 ack，等于这通电话按老契约跑，和本次改动之前一样。
鉴权放在 ack 之前（fail-closed）：被 Narra 拒绝的来电者，其通话不应进入 v15 的强绑定模式。

### 3. `_claim_voice_turn`

位置在 `_build_and_run_agent` 的语音分支入口、进 `_run_voice_serialized` 之前。严格语音轮先在渠道 dedup store 里
原子认领 `(room, session, turn)`。认领失败 = 同一逻辑轮已经跑过（Narra 换了 event id 重发）或已被取消（墓碑），
返回空串并写 `ingress_dropped_dedup` 审计。位置是有讲究的：

- 必须在**基类管线的所有门之后**（authorize-event、解绑凭据、空内容、ingress breaker）：在门之前认领的话，
  被 breaker 挡掉的轮次 key 已经烧掉，Narra 重发时反而被判重、这一轮永远没有回复。被拒的发送者也因此烧不掉 key。
- 必须在**进通话队列之前**：重复的输入要被丢弃，而不是被 `_merge_voice_batch` 合并进某一轮。

dedup 的持久层挂掉时 `classify` 是 fail-open（放行）。认领沿用这个语义——fail-closed 会让一次 DB 抖动变成通话全哑——
但会写一行 `dedup_fail_open` 审计：这一轮的「至多一次」保证此刻不成立，重发的同一轮可能被念两遍，事后得查得到原因。
这行审计另起任务写、不在认领路径上 await：走到这里说明 DB 刚超时过一次，串行再写一次会让来电者的首包再等一个超时。

认领成功且该轮属于已知的 v15 通话（未结束，房间、来电者都对得上）时，另起任务发 `turn.started` ack。
降级语音轮恒放行（沿用基类按 event id 的去重）；文字轮和 kill switch 下的语音轮根本不走这个分支。
v14 agent 永远收不到控制事件、registry 为空，所以**不会**给 v14 的房间发任何 ack。

### 4. 取消

`turn.cancelled` 做三件事：ack、墓碑（让晚到的同一轮输入认领不了）、在 registry 里记下取消。第三件是给
`_run_voice_serialized` 的 drain 循环用的：每取一批先过 `_drop_dead_voice_turns`——已取消的、所属通话已结束的、
所属通话已被房间下一通电话取代的排队轮次都被剔除并写审计。它们不会在当前轮跑完后启动，文本也不会被合并进
下一轮（包括下一通电话）的 prompt。drain 循环先把 `pending` 取走再 await 过滤，过滤期间新到的消息进新列表。

**中断正在跑的那一轮还没做**，是下一步；在那之前它的回复仍会发出，但带的是旧 `turn_id`，Narra 会按
`active_turn_interrupted` 拒掉，不会串进新轮。

## 已知边界

- ack 失败和 `stop()` 时的在途事件**不会**被可靠地重来：/sync 游标不等这些任务，而 Narra 重发用固定 txn id、
  多半被 homeserver 折叠。后果都落在安全一侧——`call.started` 没 ack 成，这通电话就留在 v14 匹配；取消没处理完，
  晚到的那一轮输入跑一次、回复被 Narra 拒。
- 重启后 registry 为空，到这通电话的下一个新控制事件之前 `turn.started` 不 ack（认领是持久的，重发的轮次不会重跑）。
- 同一批 sync 里先后到的 `call.started` 和第一轮输入：准入要等房间形状 + authorize 两次 I/O，第一轮的认领可能
  先跑完，于是它拿不到 `turn.started` ack（只少一条指标，回复不受影响）。
- kill switch 打开时没有任何 ack，但绑定并非处处一致：authorize 拒绝通知在语音标记被剥掉之前发出、带绑定；
  atomic 路径上的回复用的是已剥掉标记的消息、不带。两者都无害——没 ack 过的通话，Narra 不检查绑定。
- 被 `_merge_voice_batch` 合并掉的轮次也各自认领、各自发了 `turn.started` ack，但合并后只有一条回复、绑的是最新一轮的
  `turn_id`；被并掉的那几轮在 Narra 看来是「ack 了但没回复」。合并是既有行为，这里只是多了 ack。下一步做
  「中断正在跑的轮次」时必须把合并、ack、取消三者的关系一起想清楚。
- 被判重的重复投递会在收件箱里多写一条 inbound 记录：认领在基类管线里面，基类对返回空串的轮次照样记 inbound
  （排队被合并的语音消息一直就是这样）。要消掉需要基类提供「这一轮不记」的出口，是跨渠道改动。

测试：`tests/narramessenger_module/test_voice_control_events.py`、`test_voice_single_visible_reply.py`。
