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

托管那条是**行为断言**而不是源码字符串比对，而且**调真实的
`ManagedChannelIngress.before_run`**：跑
`build_inbound_run_context` → `before_run` → `retag_managed_input`，断言最终
送进模型的字符串里含标记。

这里踩过两个坑，都值得记：

1. **源码比对看不见「盖章顺序错了」**——盖章确实在，只是发生在渲染之后，
   所以第一版是绿的，漏掉了一个 Critical。
2. 改成行为断言之后，第二版**手抄了 `before_run` 里那三行盖章逻辑**而不是
   调它。于是把生产代码里整段盖章删掉，全套测试依然全绿——测试自己把标记写
   进了字典。**测试实现了一遍被测行为 = 什么都没测。** 现在替掉
   NarraMessenger 的 fail-closed 授权门（`managed_before_run`）再调真的
   `before_run`；「不好 mock 就退回手抄」正是这个坑本身。

计数守卫按**模块种类**分两条判据（都从注册表推导，不是字面白名单）：
trigger 模块必须传 seam 的返回值（否则下一个人可以用硬编码 `False` 把 CI
哄绿，而硬编码 False 正是这个守卫要防的失败）；托管构造点允许 `False`
（那时 trigger 还没跑），但必须同时存在重渲——少了重渲，这个 False 就不再
合法。`_code()` 用 `ast` 把 docstring 也剥掉，避免注释/示例进计数。

做过五次变异验证：拿掉 Lark 填充 → 计数守卫红；把 Lark 的 seam 调用换成硬
编码 `False` → 红；把 `retag` 变 no-op → 托管行为断言红；把 `retag` 改名
（托管那处 False 失去合法性）→ 红；**删掉生产代码里整段盖章 → 红**（这条是
第 2 个坑的回归防线）。

最后一条钉的是 **prompt 文案与标记必须一致**——协议里点名一个 tag 从不渲染
的标记，等于给模型留一条走不到的分支。
