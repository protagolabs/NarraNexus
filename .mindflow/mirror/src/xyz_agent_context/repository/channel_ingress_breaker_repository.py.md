---
code_file: src/xyz_agent_context/repository/channel_ingress_breaker_repository.py
stub: false
last_verified: 2026-08-25
---

## 2026-08-25 — 键改四段；方言解释更正；`find_open` 加 `cooling_only`

**① 键**：每行对应 `agent_id|channel|chat_id|sender_id`（本页下方旧条目写的
三段已过时）。

**② 方言解释更正**（这一条重要，因为旧说法本身就是被判定为错的）：
旧条目断言「空格形式的 cutoff 排序低于所有 'T' 开头的行，`updated_at < %s`
会一条都匹配不上」。**这句话说过头了。** 字符串是逐字符比较的，`'T'`(0x54)
与 `' '`(0x20) 的差异只在**日期部分完全相同**时才决定结果：跨天比较一律
正确，只有 cutoff 当天的行会被判成比实际新而逃过清扫。范围很窄——但同日
保留期测试恰好命中的就是它，这正是本方法第一版报告「删除 0 行」的原因。
改用 `event_time_str` 归一化两侧后这个边界完全消失。

**③ `find_open(cooling_only=...)`**：这张表只在 `tier > 0` 那一侧增长——
`cleanup_older_than_days` **只**扫 `tier = 0`，而跳闸一次后再不说话的会话
永远等不到 `_maybe_recover` 需要的 `admit()` 调用，tier 降不下来。所以
「所有 tier>0 的行」更接近一份**历史跳闸流水**而不是当前状态查询。
`warm_start` 因此只取 `cooling_only=True`（当前仍在冷却的），否则内存占用
和 `/healthz` 的计数会随部署次数单调上升。

# channel_ingress_breaker_repository.py — ingress 熔断器状态数据访问

## 为什么存在

`channel_ingress_breaker`（每个会话键 `agent_id|channel|chat_id|sender_id` 一行）的
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
