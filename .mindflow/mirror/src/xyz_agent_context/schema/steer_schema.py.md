---
code_file: src/xyz_agent_context/schema/steer_schema.py
last_verified: 2026-08-21
stub: false
---

# steer_schema.py — 运行中插话的一条注入

`SteerInjection`:路由进"已经在跑的 turn"的一条消息(而非触发新 turn)。live-steering
"往运行中 loop 追加消息"能力的持久记录。

- `run_id` **不透明**:本 schema 不知道 orchestrator 怎么标识一个 live run(那是 RunRegistry 的事),
  只要 producer 与 drainer 认同一个句柄——存储层与路由设计解耦。
- `source`(team / owner_chat)记哪个 producer 写的,供 prompt 层措辞(队友房间消息 vs 主人插话,
  机制同、措辞不同)。IM 触发 v1 不做。
- `id` 是到达序 + 消费游标单位,store 赋值故落库前为 None;`consumed_at` None=待消费,盖章=已被 run
  drain,防二次注入。注入 append-only,不改前行内容。
