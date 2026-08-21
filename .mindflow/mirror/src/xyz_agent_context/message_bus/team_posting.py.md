---
code_file: src/xyz_agent_context/message_bus/team_posting.py
last_verified: 2026-08-20
stub: false
---

## 2026-08-20 — 级联上限 4 → 30，可用 env 覆盖

`MAX_TEAM_AGENT_HOPS` 从 4 提到默认 **30**，并由 `_resolve_hop_cap()` 读环境变量
`TEAM_MAX_AGENT_HOPS` 覆盖。原因：4 跳太小，普通 team 任务需要多于四次不被打断的
agent 往返才能完成，房间到第 4 跳就剥 @mention、链断、干等人工。它**仍是有限的防
死循环保险丝**（runaway agent-to-agent @storm 是真实事故类，见 Liam A2A 死循环），
所以覆盖值为空 / 不可解析 / ≤0 时一律回落默认，**永不把保险丝关掉**；因为该值直接进
`bus_messages` 上的 SQL `LIMIT`，超过上限 500 会被 clamp（防手滑多打一个零把几行扫描
变成十万行）。窗口 `LIMIT MAX_TEAM_AGENT_HOPS + 2` 与所有测试都从常量派生，抬值自洽。
常量是模块导入期求值，改 env 需重启 workers。env 见 `.env.example` 的
「Team autonomous-round budget」段。

## 2026-08-17 — errand 记账随发帖一起搬进来

`close_delivered_errands` + `record_handoffs` 从 trigger 的 `_deliver_reply` 移入
`post_team_reply`。理由和级联上限搬家时一样：这是**往 team 房间放一条消息**的属性，
不是恰好触发了这一轮的那个循环的属性。留在原地会直接停止运行——PR #310 的交接板会
静默变空。

顺序即设计：**先关后开**。奠基消息（「收到…完成后交付 @A4」）上关是 no-op（那是承诺
不是交付），开则接上下一环，两跳都被盯住；反过来会让一次交接关掉它自己刚创建的 errand。

传给它的 `mentions` 是**上限裁剪之后**的名单：被级联上限拿掉的 @ 从没到过对方，为它
开一条 errand 等于让一个从没被请求过的人欠账。

永不抛出：回复已经在房间里、这一跳已经成功，一次记账失败不能把它变成失败。


# team_posting.py — 把 agent 的话放进 team 房间的那**一条**路

## 为什么存在

2026-08-17 之前，team 回复是 agent 的**纯文本**，由
`MessageBusTrigger._deliver_reply` 自动上墙。那让 team 房间成为全系统**唯一**一个
「纯文本没人收到」为假的 surface，而这条例外会传染：框架宪法、ChatModule 的指令、bus
模块的规则**三层同时**陈述那条通则，其中只有一层有「本轮不适用」的开关。PR #311 的六轮
评审全部花在由此长出的矛盾上。

所以房间改成像别的 surface 一样收工具调用，本模块就是那个工具调用的落点。
`_deliver_reply` 做过的每件事都搬到了这里——@mention 解析、agent 间跳数上限、turn 盖章
——因为那些是**「往房间里发帖」这件事的属性**，不是恰好拥有旧投递路径的那个 trigger 的
属性。

## 上限搬家修掉的那个洞

`MAX_TEAM_AGENT_HOPS` 此前**只**实现在 `_deliver_reply` 里，而 `message_team` 可以
直写 team 房间、完全不受计数约束。**防死循环的保险丝装在被告知不要走的那扇门上。** 搬到
这里之后它在唯一入口上。

## Gotcha

**`db` 收的是 `AsyncDatabaseClient`，不是原始 backend。** `team_cascade_depth` 是从
trigger 里搬出来的，那里 `self._bus._db` 是原始 backend、SQL 原样下发；而这里每个调用方
持有的都是 client。第一版沿用了 `db.placeholder`，而 client **没有这个属性**——于是上限
检查抛 `AttributeError` 并把整个发送带走：每次 `message_team` 都返回
`{"success": false}`，房间保持沉默。现在用 `%s` 交给 client 做方言翻译（那也正是双方言
契约想要的）。守卫见 `tests/message_bus/test_team_posting_cap.py`——**一个会抛的
loop-breaker 比没有更糟，因为发送也一起失败了。**

**`capped` 由本模块自己叙述。** `post_cascade_capped` 在这里调用，因为施加上限的是这里。
trigger 那侧原来也叙述一遍，两处都留会说两次。

## 少了什么（有意的）

`segments`（独白/回复边界）不再存在于 team 回复上。回复是工具参数，agent 的思考保持私密
——所以没有「独白那一半」可渲染。**房间里看不到 agent 出声思考了，这是本次有意移除的
特性**：它此前之所以存在，只是因为回复和思考是同一段文本。存储层的 `segments` 列语义
不变（缺失 = 一整块），公告栏与 IM 路径仍可使用。见
`tests/message_bus/test_team_message_segments.py` 同日条。

## 2026-08-18 — 差事记账抽成具名接缝 `_record_errands`

PR #310 的「交接自动上板」原本挂在 [[message_bus_trigger.py]] `_deliver_reply` 上；房间改成
工具调用后那条路径消失，记账随之搬到 `post_team_reply`。合并时它落成了一个内联 try/except，
于是**位置**（必须在 `bus.send_message` 之后）和**吞异常**这两道独立安全网变成同一个构造，
互相掩护：补丁掉 errand 函数会被吞异常挡下，即使调用已经漂到 post 之前也照样绿。

抽成模块级 `_record_errands` 后，补丁这个名字即可绕开吞异常、单独把位置置于测试之下
（`test_errand_auto_board.py::test_the_hook_sits_outside_the_post`，已做变异验证）。
顺序仍是先关后开；`mentions` 取**过级联上限之后**的列表 —— 被截掉的 @ 没送达，不该记账。
