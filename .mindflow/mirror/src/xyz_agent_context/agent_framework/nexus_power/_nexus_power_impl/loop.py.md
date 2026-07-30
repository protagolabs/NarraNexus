---
code_file: src/xyz_agent_context/agent_framework/nexus_power/_nexus_power_impl/loop.py
last_verified: 2026-07-30
stub: false
---

## 2026-07-30 — tool_use_start 不再被 continue 吞掉

`_stream_step` 原来在 `include_arg_deltas` 分支里对 tool_use_start 做完
extractor 设置就 `continue`，事件到不了账本。现在设置完落到
`ledger.record_model_event`，由账本发「名字先行」ui 事件（TYPE_TOOL_USE_START）。

# loop — 相位推进器(≤500 行门禁)

## 2026-07-30 — 流内取消:显式 aclose + 后流边界

`_stream_step` 每个模型事件前查 cancel,命中即 `await stream.aclose()` 再 break——
裸 break 会把生成器(和它的 HTTP 流)留到 GC,继续为没人读的 token 付费。配套在
MODEL_STREAM 与 DISPATCH 之间加取消边界:没有它,被中途掐断的纯文本流会落进
STOP_CHECK 关成 NO_MORE_ACTIONS——用户打断被伪装成自然结束。已流出的 delta 留在
账本,close 时折叠进 assistant 消息(打断的工作是历史,不是垃圾)。

只决定「下一步做什么」:一切分叉是策略调用、一切能力是通道调用,扩展路线图零改本文件。硬保证:取消只落安全边界且绝不切开配对(合成收口);任何终止路径恰好一个 turn_done(计费链唯一源,finally 兜底);CONTEXT_OVERFLOW→压缩+重试(有进展才重试,防死循环);本文件永不出现轮次/时长上限(铁律 #14)。参数流式:tool_use_start 建 extractor、arg_delta 喂片、tool_use finalize 校齐(流出文本==最终值不变量)。
