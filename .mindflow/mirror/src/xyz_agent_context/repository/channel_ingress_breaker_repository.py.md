---
code_file: src/xyz_agent_context/repository/channel_ingress_breaker_repository.py
stub: false
last_verified: 2026-08-28
---

## 2026-08-27（第一轮 review）— 只增不删这件事治了根因，`find_open` 加上界

本文档此前写着这张表「更接近一份终身跳闸日志」——那不是这张表的性质，是
`tier > 0` 的行没有衰减路径导致的**症状**。`cleanup_older_than_days` 只扫
`tier = 0` 是对的（带升级记忆的行正是要记住的），缺的是让沉默的会话把 tier
降回 0。现在 [[ingress_guard]] 的 `_load` 按沉默时长追补衰减并在归零时写回，
这类行自然进入清扫面。

`find_open` 加了显式上限（`_FIND_OPEN_LIMIT`）。`tier > 0` 与「是否仍在冷却」
都在 Python 侧筛，所以没有上限就是每个进程启动一次无上界全表读。**截断必须
出声**：短结果和完整结果长得一模一样，`warm_start` 会报出一个悄悄偏少的
「当前被隔离数」——静默截断永远会被读成「全都加载了」。

但**告警的判据不是行数**。上限量的是该渠道的总行数（`tier > 0` 与「是否在
冷却」都在 Python 侧筛），而 `ORDER BY cooldown_until DESC` 在两方言下都把
NULL 排最后，被切掉的尾巴通常恰好是本方法本来就要丢的那些。所以只在**保留的
最后一行的冷却仍晚于 `now`** 时才 WARNING（判据是「仍在冷却」而不是「非空」
——按 `cooldown_until DESC` 排下来，保留的最后一行完全可能带一个早已过期的
时间戳，此时被切掉的尾巴不可能含未过期的行）——那是唯一可能真有候选被挡在
上限外的情形；否则降到 debug。按行数报的话，任何跳闸历史长的渠道每次启动都会
喊一声而实际一条未丢，而每次启动都喊狼来了的告警会被加进忽略列表，那时真截断
与静默截断是同一个结局。

**以上只对 `cooling_only=True` 成立。** `cooling_only=False` 问的是另一个问题
——「有多少会话带着升级记忆」，候选集是全部 `tier > 0` 行，而被切掉的尾巴恰恰
是 `cooldown_until IS NULL` 的那批、其中完全可能有 `tier > 0`。所以那条路径上
**任何截断都要出声**。两条路径共用一句「没丢候选」的保证，会给其中一条反向的
承诺。

`upsert_state` 仍是「先读后写」，非原子。同一 session key（含 `agent_id`）的
写入实际只来自单个 trigger 进程的单条协程，撞唯一索引的异常也被兜住、只丢
一次持久化而内存侧仍在执行冷却（fail-open 是这里正确的一侧）。若托管路径与
原生路径将来同时服务同一 agent，改用仓库已有的 upsert 写法，比加锁便宜。

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
