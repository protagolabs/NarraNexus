---
code_file: plugins/builtin.channels.narramessenger/src/narranexus_plugins/narramessenger_module/_voice_call.py
stub: false
last_verified: 2026-11-19
---

## Why it exists

Narra 语音契约 v15 的**控制面**在 Nexus 侧的纯逻辑层：哪些 Matrix 自定义事件算控制事件、`ack.v2` 长什么样、
一通电话在本进程里怎么记、控制事件怎么限速。没有任何 I/O——传输、鉴权、持久化的认领都在 [[matrix_trigger.py]]，
这样契约形状可以不起 homeserver 就单测。

契约源：Narra 的 v15 语音通话契约交接文档（2026-11-19）§3.0、C3–C7、C12。

四个下行控制事件只发给 installed_version ≥ 15 的 agent，由**来电者自己的 Matrix 身份**发进通话所在的 1:1 房间：
`call.started.v2` / `turn_hint.v2` / `turn.cancelled.v2` / `call.ended.v2`。`turn.started.v2` 不是独立事件，
它嵌在语音输入那条 `m.room.message` 里，走消息路径。控制事件**不是用户提示，永远不触发推理**。

## Design decisions

- **ack 形状写死在这里，一字不差。** Narra 对不合规的 ack 是静默忽略、不回错误，所以形状不能靠调用方拼：
  call 级 ack（`call.started` / `call.ended`）**不能有 `turn_id` 这个 key**（连 null 都会整条被拒），turn 级必须有。
  `platform` 报 `nexus`、`call_mode` 报 `realtime`（trigger 是持续的 /sync long-poll）、
  `supports_long_task_delivery` 报 `False`——后台交付（dm_report）没实现，报 true 就是谎报能力。
- **ack 的 txn id 是确定性的。** 同一个逻辑 ack 可能发不止一次（控制事件真的到了两次，或我们自己发送失败后重试）；
  固定的 txn id 让 homeserver 把它们折叠成一条房间事件。用 sha256 是因为契约明说 id 是不透明字符串，
  别依赖 UUID 格式/长度/字符集。
- **`voice_turn_claim_key`：认领和墓碑是同一把 key。** 一轮在跑模型/工具之前先在渠道 dedup store 里原子认领
  这把 key；`turn.cancelled` 也去认领同一把 key——于是「晚到的被取消轮次」自然认领失败、不会重启。不需要另一张表。
  key 是哈希：要塞进 `channel_seen_messages.message_id`（128 字符，还要拼 agent_id 前缀）。**房间也进 key**：
  一通电话只在一个房间里，不带房间的话，别的房间的成员只要知道 id 就能在自己房间里把这一轮的 key 烧掉。
- **墓碑挡不住「已认领、还在排队」的轮次，所以 registry 另记一份可查询的状态。** 同一房间的语音轮是串行的：
  T1 在跑时 T2 已经认领并排队，此时来的 `turn.cancelled(T2)` 认领同一把 key 只会得到「重复」，对排队中的 T2
  毫无作用。`VoiceCall.cancelled_turns` + `ended` 给 trigger 的 drain 循环一个能问的地方（`turn_is_dead`）。
- **一个房间一通「当前」电话；旧的是「退役」，不是「忘掉」。** 这是整个 registry 最容易做错的一点。
  房间的轮次队列是**跨通话共用**的：用户挂断后几秒内重拨，上一通的 T2 可能还排在队里。如果学到新 session 时
  把旧条目直接删掉，`turn_is_dead` 就查不到它、判为活的，T2 照跑，文本还会被合并进新通话第一轮的 prompt。
  所以每个 (agent, room) 留一个有上限的退役 session 列表（最近 32 个）：退役 session 的轮次恒为死；
  退役 session **永远不能再变回当前**（`learn` 返回 None）——旧通话的晚到事件不能把正在进行的通话挤掉。
  `call.ended` 只标 `ended`、不退役，直到房间下一通电话到来才退役。
- **TTL 到期是「忘掉」，不是「退役」。** 退役的含义是「这通电话结束了，它的轮次都是死的」，而年龄本身证明不了这一点：
  一通超过 7200 秒还在继续的电话必须继续被回答。到期只让它丢掉 ack，直到它的下一个控制事件把同一个 session
  重新学回来（被忘掉的 session 可以再学，退役的不行）。被忘掉时它的已取消 / 已结束状态也一起丢掉，
  只影响恰好在 7200 秒边界上还排着队的轮次。
- **`turn_is_dead` 对「本房间没有任何记录的 session」返回 False。** 这一条必须守住：v14 agent 的每一轮、
  重启后的每一轮、以及新通话里抢在 `call.started` 准入完成之前到达的第一轮，都属于这种情况；判死就是把正常语音轮丢掉。
  所以**不**做「不是当前 session 就算死」的推断，只认明确退役过的。
- **registry 从任意一个控制事件学会 session，不只是 `call.started`。** 控制事件只会到 v15 agent 手里，看到一个
  就等于证明这通电话走 v15。registry **只在内存**：它不是去重的真源（真源是持久化的认领），丢了只是少几个 ack。
- **session 钉在第一次见到它的房间 + 来电者上。** 契约 C3：同一 `rtc_session_id` 的后续事件必须来自同一房间、
  同一来电者。`learn()` 对已是当前的 session 返回**原有**条目而不是覆盖，调用方必须拿返回值再核对一次——
  两个房间并发准入同一个 session 时，后到的那个靠这一步被挡下。
- **id 有长度上限（128，定义在 [[_rtc_voice.py]] 的 `MAX_RTC_ID_LEN`）。** `rtc_session_id` / `turn_id` 完全由房间对端决定，
  而它们会进 registry、退役列表、`cancelled_turns`、审计行和日志。只限条数不限长度的话，一个已授权的来电者用
  几十 KB 的 `turn_id` 连发取消，就能把多租户共享的 trigger 进程撑出几十 MB 常驻内存。超长一律按 malformed 拒绝，
  **不截断**：认领 key 和 ack 的 txn id 是这些 id 的哈希，截断会让两个不同的轮撞上同一把 key。
- **大小都有界。** 当前通话数 ≤ 房间数；每房间退役列表 ≤ 32（远多于一个房间在 5 分钟「旧事件仍被接受」窗口内
  能打完的通话数，所以退役 session 不会在它的事件还可能到达时被挤出列表）；每通电话的 `cancelled_turns` ≤ 1024
  （满了 `mark_cancelled` 返回 False，trigger 丢弃该事件）；当前通话 7200 秒后被忘掉（Narra 端通话记录的最长寿命）。
- **`VoiceRateLimiter`：按 (agent, scope) 的滑动窗口。** scope 通常是房间 id；trigger 里「每个 agent 每分钟一行」的
  审计节流把原因名当 scope 传，让每种原因各有自己的窗口。每 256 次 `check` 清一遍状态已完全过期的 key，
  所以表里只留最近活跃的房间，而不是进程见过的所有房间；拒绝时间戳要等自己也过了窗口才清，
  否则一场还在持续的洪泛会被重新报成「第一次被拒」。 控制事件不走基类的 ingress breaker 和有界 worker 池，
  得自带限速。trigger 用两个实例：房间内**所有**控制事件（默认 120/分钟），以及**未知 session 的准入**
  （默认 6/分钟——每次准入要查房间形状 + 调一次 Narra `authorize-event`）。`check()` 区分「本窗口第一次被拒」
  和「之后的被拒」，让 trigger 对一场洪泛只写一行审计，而不是每个事件一行。阈值远高于一通正常电话的量
  （每轮至多 hint + cancel，加上 started / ended），所以 8 秒响铃窗口内的 `call.started` ack 不会被限速挡住。
- **审计事件名定义在本文件**（`voice_control_handled` / `voice_control_dropped`）：渠道专属，不进通用的
  channel 审计词表；审计列是 VARCHAR，不需要迁移。

## 没做的

- `turn_hint` 只 ack、不存：它目前没有消费方（不强制后台交付，也不重新推理）。
- `progress.v2`、长任务 `dm_report`：契约里是可选项，第一期不做。
- `call.started` 里的 `deadline_ms` / `voice_rules`：`voice_rules` 与每轮 `voice_instructions` 是同一段规则，
  已经由语音输入路径应用；`deadline_ms` 目前没有消费方。

## Downstream

只被 [[matrix_trigger.py]] 消费。测试：`tests/narramessenger_module/test_voice_control_events.py` 的 pure layer 部分。

## 已知限制

- 重启后 registry 为空。Narra 重发未 ack 的控制事件时用固定 txn id，homeserver 多半把它折叠回原事件、/sync 里
  不会再出现，所以**不能指望对端把 registry 教回来**；要等这通电话的下一个**新**控制事件。期间 `turn.started` 不 ack，
  回复照常带绑定、照常被接受。重启也清空了轮次队列，所以不存在「重启后旧轮次复活」。
- 旧轮次的输入如果在新轮次开始之后才**第一次**到达（不是重发，所以没有墓碑），它仍会被认领并跑一次；Narra 会按
  「不是当前轮」拒掉它的 ack 和回复。要挡住需要比较 `turn.started.v2` 的 `sent_at`，目前没做。
