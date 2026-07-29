---
code_file: src/xyz_agent_context/agent_framework/nexus_power/_nexus_power_impl/modeling/model_client.py
last_verified: 2026-07-29
stub: false
---
# modeling/model_client — litellm chunk→ModelEvent 翻译层

LitellmClient 管连接透传,本类管语义:cache_plan 按方言注入 cache_control、usage 双词汇归一(OpenAI cached_tokens 含在 prompt 内→换算为 exclusive)、tool_use_start 名字先到(E3 时序安全)。重大坑:自定义 base_url 时强制 anthropic/ 前缀——模型 id 自带斜杠(minimax/minimax-m2.5)会被 litellm 误当 provider 路由(实测事故已修)。
