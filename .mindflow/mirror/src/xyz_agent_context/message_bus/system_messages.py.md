---
code_file: src/xyz_agent_context/message_bus/system_messages.py
last_verified: 2026-08-11
stub: false
---

# system_messages — 平台自述行的唯一真源

## 为什么存在

团队房间里有两类消息：agent 和人在说话，以及**平台在讲自己刚做了什么**（某个 run 被停了、
公告栏变了、巡查扫了一遍板子）。几乎每个要统计、采样、总结房间活动的消费者都得排除第二类，
而它们此前各自重打字符串字面量。

于是出了教科书式的两次事故，**方向还相反**：

- 总结 worker 里写了 `system_stop` 和 `system_bulletin`；`patrol` 来自另一个功能、晚一步落地，
  没有任何东西告诉 worker——一个专门用来阻止「平台自己触发自己」的过滤器，**从它不知道的那一侧被重新打开**。
- `_team_cascade_depth` 恰好相反：排除了 `patrol`，没排除另外两个。

两边各漏各的，而两边都以为自己写全了。

## 设计

元组放这里，消费者 import。加第四种类型是一次编辑，**而不是"记得去改三处 SQL 字符串并祈祷三处都找到了"**。

`placeholders()` 按元组长度生成占位符：此前三处查询把 `(%s, %s)` 的个数硬编码进 SQL 字面量，
那是又一处需要同步、又一处会漏。

**定义仍留在各自的功能里**（`patrol.py` / [[team_bulletin]]），本模块只做汇总——
这样新增一种类型不需要先搬家。也避开了环：`team_bulletin` 不能 import 本模块。

相关：[[message_bus_trigger]]（cascade 深度）、[[team_summary_worker]]（触发计数与总结素材）
