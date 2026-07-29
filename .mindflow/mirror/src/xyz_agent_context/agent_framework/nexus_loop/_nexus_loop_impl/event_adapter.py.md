---
code_file: src/xyz_agent_context/agent_framework/nexus_loop/_nexus_loop_impl/event_adapter.py
last_verified: 2026-07-29
stub: false
---
# event_adapter — 遗留 dict 契约唯一翻译点

灰度共存的护城河:六种遗留形状只在这里产出,旧消费链(ResponseProcessor/前端/计费/审计)零改动。step_done→response.usage(每步用量,代理模型记账兜底同款通道);turn_done→response.done(双词汇 usage)。tool_arg_delta/compaction 无遗留形状,有意丢弃(新协议消费方读类型化流)。
