---
code_file: src/xyz_agent_context/agent_framework/nexus_power/_nexus_power_impl/modeling/model_client.py
last_verified: 2026-07-30
stub: false
---

## 2026-07-30 — _parse_args 不再静默 {"_raw": ...}

坏参数 JSON 显式随 tool_use payload 的 parse_error 字段出去((args, err) 双返回),
args 恒为 {}。旧的 _raw 兜底让截断调用带着丢失的字段继续执行,下游报错全部
指向错误方向。解析失败信息带 char 位置与总长,供模型判断截断点。

# modeling/model_client — litellm chunk→ModelEvent 翻译层

LitellmClient 管连接透传,本类管语义:cache_plan 按方言注入 cache_control、usage 双词汇归一(OpenAI cached_tokens 含在 prompt 内→换算为 exclusive)、tool_use_start 名字先到(E3 时序安全)。重大坑:自定义 base_url 时路由必须**显式写死**——模型 id 自带斜杠(minimax/minimax-m2.5、deepseek-ai/DeepSeek-V3)会被 litellm 误当 provider 前缀。但写死的是 provider 决定的**协议**,不是「有 base_url 就 anthropic」:后者让 openai 协议的卡回 AnthropicException(实测)。tool 方言重写同理,只在 anthropic 路由上做(绕开严格网关对 type:"custom" 的 serde 拒绝),openai 端原样透传。
