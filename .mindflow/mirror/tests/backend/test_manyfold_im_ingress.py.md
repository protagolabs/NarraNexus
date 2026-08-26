---
code_file: tests/backend/test_manyfold_im_ingress.py
stub: false
last_verified: 2026-08-26
---
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
