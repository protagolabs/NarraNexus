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

做过变异验证：拿掉 Lark 那处填充，这条守卫立刻变红。

最后一条钉的是 **prompt 文案与标记必须一致**——协议里点名一个 tag 从不渲染
的标记，等于给模型留一条走不到的分支。
