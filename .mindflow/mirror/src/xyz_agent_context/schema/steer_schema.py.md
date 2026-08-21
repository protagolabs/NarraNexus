---
code_file: src/xyz_agent_context/schema/steer_schema.py
last_verified: 2026-08-21
stub: false
---

# steer_schema.py — 运行中插话的一条注入

`SteerInjection`:路由进"已经在跑的 turn"的一条消息(而非触发新 turn)。**这张表的正当理由是
解耦,不是持久化**——team 消息本来就在 bus_messages,单为持久化再存一份是冗余;但**不仅仅是
bus**:单聊插话走 chat 记忆路径、根本不进 bus_messages,所以"查 bus_messages"会漏掉一半
producer。统一 inbox 让 feeder 只 drain 一处(同 instance_artifact_events 的 outbox 模式)。
`consumed_at` 是 bus 给不了的**per-run 游标**(bus 的 (agent,channel) 游标是 trigger 的,
且一个 agent 可能多 run 并发)。

- `run_id` **不透明**:本 schema 不知道 orchestrator 怎么标识一个 live run(那是 RunRegistry 的事),
  只要 producer 与 drainer 认同一个句柄——存储层与路由设计解耦。
- `source`(team / owner_chat)记哪个 producer 写的,供 prompt 层措辞(队友房间消息 vs 主人插话,
  机制同、措辞不同)。IM 触发 v1 不做。
- `id` 是到达序 + 消费游标单位,store 赋值故落库前为 None;`consumed_at` None=待消费,盖章=已被 run
  drain,防二次注入。注入 append-only,不改前行内容。
