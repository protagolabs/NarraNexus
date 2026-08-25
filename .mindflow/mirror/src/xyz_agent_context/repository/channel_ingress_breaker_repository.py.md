---
code_file: src/xyz_agent_context/repository/channel_ingress_breaker_repository.py
stub: false
last_verified: 2026-08-24
---
# channel_ingress_breaker_repository.py — ingress 熔断器状态数据访问

## 为什么存在

`channel_ingress_breaker`（每个会话键 `channel|chat_id|sender_id` 一行）的
CRUD。[[ingress_guard.py]] 拥有全部升级逻辑，这一层只读写行——与
[[agent_circuit_breaker_repository.py]] 和它的熔断器服务是同一种分工。

读写量刻意做得极小：guard 对一个 key **只读一次**（进程内首次见到时懒加载），
只在**层级变迁**时写。热路径——每一条入站消息——永远不碰这一层。

## 设计决策

**`get` / `upsert_state` 都不抛。** 守卫不是授权门；DB 出问题应该降级成
「不知道持久状态」（guard 退回纯内存工作），而不是把整条入站路径拖下水。
丢掉一次变迁的持久副本，代价是那一个会话失去重启存活能力，内存里的冷却
在本进程生命周期内仍然生效——比起放倒 ingress，这个代价小得多。

**`cleanup_older_than_days` 只扫 `tier = 0` 的行。** 带着升级记忆的行正是
我们承诺要记住的东西；因为它安静就删掉，等于给一个惯犯发新预算——那恰好
是这张表存在的目的所反对的。

**时间比较在 Python 里做，不写进 WHERE。** 两种 dialect 对 `updated_at`
的拼法不一致：sqlite 回读是 `2026-08-24T10:29:33.197094+00:00`（isoformat
默认的 'T'），而 `channel_seen_messages` 那种空格形式的 cutoff 字符串排序
**低于**所有 'T' 开头的行——`updated_at < %s` 会一条都匹配不上，然后清扫
心满意足地报告删除了 0 行。第一次实现就踩了这个坑，冒烟测试才发现。
`utils/db/dialect_time.py::event_time_str` 就是 DB 层为这个不对称准备的答案。
