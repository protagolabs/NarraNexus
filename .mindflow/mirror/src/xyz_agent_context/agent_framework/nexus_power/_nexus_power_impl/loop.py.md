---
code_file: src/xyz_agent_context/agent_framework/nexus_power/_nexus_power_impl/loop.py
last_verified: 2026-07-29
stub: false
---
# loop — 相位推进器(≤500 行门禁)

只决定「下一步做什么」:一切分叉是策略调用、一切能力是通道调用,扩展路线图零改本文件。硬保证:取消只落安全边界且绝不切开配对(合成收口);任何终止路径恰好一个 turn_done(计费链唯一源,finally 兜底);CONTEXT_OVERFLOW→压缩+重试(有进展才重试,防死循环);本文件永不出现轮次/时长上限(铁律 #14)。参数流式:tool_use_start 建 extractor、arg_delta 喂片、tool_use finalize 校齐(流出文本==最终值不变量)。
