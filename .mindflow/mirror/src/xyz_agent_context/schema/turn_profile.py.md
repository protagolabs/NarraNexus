---
code_file: src/xyz_agent_context/schema/turn_profile.py
stub: false
last_verified: 2026-08-06
---

## Why it exists

F28 语音快速回答模式的**每 turn 旋钮束**：一个 frozen pydantic 值对象，沿既有
纯 kwargs 链（run_stream → AgentRuntime.run → RunContext → TurnInput →
driver kwargs → executor wire）整体传递，替代散落的布尔开关。

## Design decisions

- **缺省=现状**是硬契约：`profile=None` 与「全默认值的 profile」对每个消费者
  必须不可区分（tests/schema + tests/agent_framework/test_nexus_turn_profile
  钉住）。fast mode 是纯加法。
- **全参数化带默认值**（Owner 2026-08-06 明确要求）：narrative 策略 / prompt
  面 / reasoning 档位 / 回复工具全部是字段，不硬编码不埋 env。
- `voice_fast()` 工厂承载 v1 决议：FULL prompt（不裁上下文）、工具面不裁、
  reasoning low（网关 DeepSeek reasoning 参数 2026-08-06 就绪）、强制
  nexus_power、reply_tool=speak。
- 跨 executor wire 用 `model_dump()` dict；nexus adapter 收 dict 或模型均可
  （单点归一化）。

## Downstream

nexus_agent._build_request_payload（prompt_mode / reasoning_effort→llm_extra /
include_arg_deltas）、step_3（framework_override）、后续 step_1 fast 分支
（narrative_strategy）与 trigger voice 检测（构造方）。
