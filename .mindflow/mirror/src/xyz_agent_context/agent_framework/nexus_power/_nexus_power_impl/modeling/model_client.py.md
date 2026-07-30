---
code_file: src/xyz_agent_context/agent_framework/nexus_power/_nexus_power_impl/modeling/model_client.py
last_verified: 2026-07-30
stub: false
---
# modeling/model_client — litellm chunk→ModelEvent 翻译层

LitellmClient 管连接透传,本类管语义:cache_plan 按方言注入 cache_control、usage 双词汇归一(OpenAI cached_tokens 含在 prompt 内→换算为 exclusive)、tool_use_start 名字先到(E3 时序安全)。重大坑:自定义 base_url 时路由必须**显式写死**——模型 id 自带斜杠(minimax/minimax-m2.5、deepseek-ai/DeepSeek-V3)会被 litellm 误当 provider 前缀。但写死的是 provider 决定的**协议**,不是「有 base_url 就 anthropic」:后者让 openai 协议的卡回 AnthropicException(实测)。且路由前缀**无条件前置、不做 startswith 豁免**——平台 id 本身可以以路由名开头(NetMind 的 anthropic/claude-sonnet-5、openai/gpt-5.4),litellm 恒吃掉第一段,豁免会把裸名发上游,NetMind 无裸名 alias 直接 404 unknown model(2026-07-30 dev 事故);双前缀外层被 litellm 消费,完整平台 id 才能上线。tool 方言重写同理,只在 anthropic 路由上做(绕开严格网关对 type:"custom" 的 serde 拒绝),openai 端原样透传。
