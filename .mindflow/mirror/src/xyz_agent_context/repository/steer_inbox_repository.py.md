---
code_file: src/xyz_agent_context/repository/steer_inbox_repository.py
last_verified: 2026-08-21
stub: false
---

# steer_inbox_repository.py — 运行中插话注入的持久 owner

live-steering 的存储层:producer 往 `steer_inbox` append 一条注入(keyed by 不透明
`run_id`——RunRegistry 的运行句柄,本层不解释),transport 在下个 step 边界把某个 run 的
未消费行 drain 进它的 SteeringInlet 并 mark_consumed。**存在理由=解耦 + per-run 游标,不是
持久化 team 消息**(那本来就在 bus_messages):不仅仅是 bus,单聊插话不进 bus,统一 inbox 让
feeder 只 drain 一处(同 artifact_events outbox);`consumed_at` 是 bus (agent,channel)
游标给不了的 per-run 游标。详见 [[steer_schema.py]]。

## 四个操作

- `append` 幂等:**原子 INSERT-or-detect-UNIQUE**(同 channel_seen 的 mark_seen,**无前置 SELECT**)——
  插入,`is_unique_violation`(双方言共用判据)**只吞唯一约束违反**→返 False,其它错误(连接/schema)
  照样 raise。去掉前置查询后,幂等测试才真正覆盖到 except 分支(之前被 pre-check 短路,那条分支永远
  跑不到)。
  **写边界三道闸(铁律 #16:拒绝,绝不丢/截断)**:① VARCHAR 列(run_id/msg_id/sender_id/role)超
  `varchar_width` 就 raise ValueError——**只拒不夹断**(两条超长 id 夹成同一条会破坏去重;role 也无理由夹);
  SQLite 静默接受、MySQL 1406,不挡就本地全绿上云全炸。source 是 Literal 靠类型兜底、content 靠 ②。
  ② content 超 `MAX_CONTENT_BYTES` raise。③ 单 run 未消费
  ≥ `MAX_UNCONSUMED_PER_RUN` 抛 `SteerInboxFull`(back-pressure,让 producer 排队/回压)。bound 放在
  写边界是 steering.py 契约明写的(drain 侧整批取不设限)。
- `pull_unconsumed(run_id)`:该 run 的 `consumed_at IS NULL` 行,按 id 升序=到达序(FIFO);**不 LIMIT**
  (drain 不截断,铁律 #16)。
- `mark_consumed(run_id, up_to_id)`:给 id ≤ 游标且未消费的行盖章,返回条数;scoped 到 run_id 且只碰
  未消费——不动别的 run,重复消费 no-op。id 上限让 drain 只消费它看见的窗口、之后到达的留 pending。
  盖章走 `to_datetime6_literal`,与 created_at 的 `datetime('now')` 默认**同字节格式**(raw 参数不过 dict
  序列化,格式得手动统一,否则 SQLite 上两种格式不可比)。
- `cleanup_older_than_days(days)`:家族 retention 契约(同 channel_seen/lark_seen/channel_trigger_audit,
  后续 PR 挂到每日 cleanup tick)。两道保险:`consumed_at IS NOT NULL`(长跑 run 未注入的绝不删,铁律 #16);
  按 `created_at` 裁(格式统一),不按 consumed_at。best-effort,驱动错吞成 0。

## 投递语义(单 drainer)

`consumed_at` 给的是**单 drainer 前提下**的 at-most-once(一个 run 一个 loop 一个 transport,即设计)。
`pull` 与 `mark` 之间无 claim,两个 drainer 同 run 会各注入一遍(同 artifact_events 的 at-least-once 取舍)。
若将来一个 run 可能有两个 drainer(executor 被 reaper 回收后替身接管同 run),改 claim-then-read
(带条件 UPDATE 的 affected_rows 当 claim,见 job_repository.try_acquire_job),别只靠 consumed_at。

## 坑

不是 BaseRepository 子类:自增 id、无实体主键、全是 scoped range 查询(同 artifact_event 的理由)。
读仍返 `SteerInjection` 给 provenance 带类型。双方言 `%s` 参数化,标识符别加引号。`cleanup`/COUNT 的生
SQL 有 MySQL twin 覆盖。

## 2026-08-23(补)— mark_consumed_by_msg_ids:按 msg_id 精确消费

新增 `mark_consumed_by_msg_ids(run_id, msg_ids)`:按**确切的 (run_id, msg_id) 集合**盖 consumed_at(而非 `mark_consumed`
的行-id 天花板),因为消费端(loop)报的是它 drain 的 msg_id,不必把行 id 穿过 transport 回来(`append` 仍返 bool)。
一次 drain 取整个队列窗口,故「精确集合」与「天花板」等价、但更直接。`IN (%s,…)` 变长占位符=新 raw SQL→配了
MySQL twin(`test_steer_inbox_mysql`)。scoped `consumed_at IS NULL`(重复消费 no-op)、`to_datetime6_literal`(与
created_at 同字节格式)。**这是 steer_inbox 第一个真正的生产消费点**——补上了「bus 写进来的行永远 NULL、retention
永不生效」的 #2 洞。
