---
code_file: tests/backend/test_manyfold_im_ingress.py
stub: false
last_verified: 2026-08-28
---

## 2026-08-28（接线）— 托管面的熔断与铁律 #16 门槛

托管模式**绕开整条原生接收路径**——没有 `_subscribe_loop`、没有 dedup store、
没有 worker 队列、没有 `_process_message`——所以基类在 `start()` 里构造的 guard
在这里永远不会生效。没有自己的调用点的话，Manyfold 这个面就是唯一没有防护的
入口。

新增两条钉铁律 #16：**每条被丢的消息留一行**（不是每次跳闸一行——那答不出
「安静了多久、吞了多少」），且行里带得出「为什么」和「还要多久」。

写第二条时抓到一处真缺口：丢弃行的 `cooldown_seconds` 恒为 0——那个字段是
跳闸时赋的长度，丢弃时没人填。补了 `cooldown_remaining_seconds`，口径在所有
行上一致：跳闸行是全长、丢弃行是剩余、其余是 0（因为确实没在隔离）。

原生面的同一道门槛在 [[test_ingress_breaker_audit_trail.py]]——两个面走**不同
的审计调用点**，把原生那侧改成不写，这里的用例照样全绿。
# test_manyfold_im_ingress.py — 托管 IM 入站的映射与 route 接线

覆盖 `build_inbound_run_context` 的 provider 映射（channel_provider /
channel_context → WorkingSource + ChannelTag + extra_data）、
`ManagedChannelIngress` 的业务 hook 路由，以及 `/v1/chat/completions` 把映射
结果交给 `BackgroundRun.drive` 的那一段接线。

## 2026-08-26 — agent-sender 标记必须活过 route

新增两条**真实端点**测试（ASGITransport + `compat_app` 夹具）。

它们存在的理由是别处测不到：`run_input` 在 ingress hooks **之前**就渲染完
了，所以只有 route 能在 hooks 之后把它重建。而其余关于
`retag_managed_input` 的断言都是**直接调那个函数**——把
`openai_compat.py` 里那行调用删掉，那些断言照样全绿，标记却从模型读到的
字符串里消失了。

断言落在 `drive` 的 `input_content` 上，也就是真正送进 agent 的那个串。
变异验证：删掉 route 那行 → 立刻红。

替掉 NarraMessenger 的 `managed_before_run`（fail-closed 授权门）是必要的：
授权是另一个关注点，放它跑会 deny 掉这一轮，然后还要一个 db 去写 deny 审计。
**替掉不等于手抄**——盖章与重渲都走真实代码。

只覆盖 narramessenger：它是全仓唯一答得出 `is_agent_peer` 的渠道；其余托管
渠道恒 False、重渲前后字节等价，由 `test_agent_peer_signal.py` 那条
「人类托管 turn 字节不变」钉住。
