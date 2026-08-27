---
code_file: tests/channel/test_ingress_guard.py
stub: false
last_verified: 2026-08-24
---
# test_ingress_guard.py — 熔断器状态机

钉 [[ingress_guard.py]] 的 L2/L3 模型：进入条件取合取、按表升级、到期只放
一条探测、表现好就衰减回闭合。

**时间风格抄 [[test_credential_breaker.py]]**：不 sleep，不往 asyncio 里打
fake clock。`admit` 收显式 `now`，所以每一条时间断言都是在一个固定基准
时刻上做算术。

**写这批测试时纠正过两个自己的错误认知**，都留在了断言里：

1. 冷却是从**跳闸那条消息**开始算的，不是从 burst 起点。按 `BASE + 冷却 + 1`
   去探测会落在冷却里。
2. 直觉以为「两条正文交替 20 次」重复率是 0.5，实际 `1 - distinct/count`
   给出 0.9。想清楚之后确认公式对、直觉错——**两条台词的乒乓依然是乒乓**。
   `test_two_bodies_alternating_forever_is_a_loop_for_everyone` 就是把这个
   结论钉下来的那条。0.5 那一档对应「每句说两遍」，那才是人类行为。
