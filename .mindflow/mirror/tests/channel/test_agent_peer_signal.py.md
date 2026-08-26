---
code_file: tests/channel/test_agent_peer_signal.py
stub: false
last_verified: 2026-08-26
---
# test_agent_peer_signal.py — 一个定义、处处填上、到达模型

钉三件事，缺一个这个信号就没有价值：

1. **seam 答得出**（每渠道一个定义，默认「人」是安全方向）
2. **每个 ChannelTag 构造点都填**
3. **tag 真的渲染出来**，模型能看见

**第 2 条是最脆的**，所以那条守卫是数量比对：**构造点数量必须等于填充数量**。
漏填不会报错，只会静静地报「这是人」——与 `build_trigger_extra_data` 当年
那个缺陷类完全同形（四处手抄、新键只加了一处，导致 Lark p2p 和
NarraMessenger DM 悄悄失去 DM 兜底）。

守卫的扫描面**从 `CHANNEL_TRIGGER_MAP` 反推**，不是写死模块列表——第一版
写死三个模块，因此看不见第四个构造点（`backend/routes/manyfold/sync.py`），
而那个正是**模型真正读到的** tag。写死列表的守卫给的是「CI 会拦我」的错觉，
新渠道会直接走过去。另有一条对账：注册表数量必须等于已注册类名数量，否则
「渠道没装 = 守卫不检查」。

托管那条是**行为断言**而不是源码字符串比对：跑一遍
`build_inbound_run_context` → 盖章 → `retag_managed_input`，断言最终送进
模型的字符串里含标记。源码比对看不见「盖章顺序错了」——第一版就是这样漏掉
一个 Critical 的（盖章在，但发生在渲染之后）。

做过两次变异验证：拿掉 Lark 那处填充，计数守卫变红；把 `retag` 变成 no-op，
托管那两条行为断言变红。

最后一条钉的是 **prompt 文案与标记必须一致**——协议里点名一个 tag 从不渲染
的标记，等于给模型留一条走不到的分支。
