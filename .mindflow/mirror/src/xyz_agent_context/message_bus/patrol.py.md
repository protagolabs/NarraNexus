---
code_file: src/xyz_agent_context/message_bus/patrol.py
last_verified: 2026-08-10
stub: false
---

# patrol.py — 让流程有人负责的那一半(平台侧)

## 为什么存在

team 房间是**纯 @ 驱动**的:每个成员(lead 也不例外)只在被 @ 时醒来。于是
一条流程能往前走,全靠每一棒都记得 @ 下一棒;任何一环忘了、静默了、或者 @ 被
级联上限剥掉,整条链就死了 —— 而且**结构上保证没有任何人会发现**。Dunhuang
断链就死在这:A3 应了声「收到」之后再无音讯。

本模块是那个「周期性激活」。放在这里的是**不能由模型说了算**的部分:判定某项
卡住,判定某个 team 到期该巡查。lead 的自由裁量从这两条之后才开始。

## running ≠ stalled,这条区分最贵

`detect_stalled_items` 认三种卡住(负责人 idle 且项未完成 / 声称 running 但
心跳死 / 压根没有 activity 行),但**心跳新鲜的 running 一律不算**,不管它跑了
多久。25 分钟的思考是**工作**;去催它就是平台成为铁律 #14 要保护的那个长任务
的打断源。

判据全部来自 `bus_agent_activity`,写回 `status=stalled` 而不是每次现算 ——
巡查提示词因此读到的是平台事实,UI 也能显示同一个词。恢复也写回:成员回来了
就退出 stalled 集合,否则会一直催一个已经在干活的人。

未认领的项不算卡住:没人接的活不存在「谁迟到了」,那需要的是派活,是另一段
提示词。

## 成本模型就是候选查询

`teams_due_for_patrol` 三道闸,按成本排序:有未完成项 → 有 lead 且没关 →
到期了。**板子空 = 零候选 = 零 run**,这是整个功能的成本保证。形状照抄
`_agents_with_pending`:一次扫全量,而不是逐个 team 去问它有没有事干。

`patrol_enabled` 为 NULL 读作**开**(对有 lead 的 team):设 lead 这个动作
本身就是在说「这个负责」。没有 lead 就不巡查 —— 平台不替用户指定负责人。

节奏自适应:常规 600s,有 stalled 项 180s(那正是流程已死而无人知晓的窗口)。

## 频控为什么必须落盘

拍板口径 (a) 让巡查消息**豁免级联深度上限**——这是它能起作用的前提:流程断掉
的现场本来就是一长串没有用户插话的 agent 消息,depth 早已到顶,不豁免的话
催办的 @ 会被无声剥掉。

代价是巡查从此不在「防 @ 风暴」的保护范围内,这个计数器成了**仅剩的兜底**。
而 bus 现有的限流活在 `MessageBusTrigger._rate_counters` —— 内存 dict +
`time.monotonic()`,workers 一重启就清零。**一重启就消失的兜底不是兜底**,所以
`patrol_spoke_at/count` 在 DB 里。

`may_patrol_speak` **fail open**:巡查的职责就是把「出事了」说出来,因为自己的
记账行读不到就闭嘴,等于在最该说话的那次把消息丢了。误放行的代价是房间里多一
行,误拦截的代价是一条流程无声死掉。

## 上下游

- **上游**:`MessageBusTrigger._dispatch_patrols`(poll cycle 的第二个候选源)
- **下游**:[[team_work_repository]](板子)、`_bus_activity` 的 `is_live`
- **相邻**:`PATROL_MSG_TYPE` 被 `_team_cascade_depth` 用来跳过巡查行

## 2026-08-10 — `patrol_is_on` 改从 `team_schema` 引入

只是引入位置变了,判定规则一字未动:那条规则读的两个字段都属于 Team,所以归
Team 的 schema 管(见 `team_schema.py.md`)。本文件仍是它唯一的 agent 侧调用方。
