---
code_file: src/narranexus/contracts/llm_client.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — helper LLM 客户端契约（用户点名的「LLM client」轴）

三个现有 SDK（`anthropic_helper.AnthropicHelperSDK` / `adapters.openai_agents.OpenAIAgentsSDK` /
`cli_helper.CliHelperSDK`）共享 `llm_function` / `llm_stream` 同构接口，`helper_sdk.py` 用协议键
分派。本文件把那个隐式同构写成 `LlmClient` Protocol，签名以三者实际参数为准
（`instructions, user_input, output_type, model, agent_id, db, reasoning_effort`）。
`model` 明文标注为「调用方偏好，客户端可忽略」——这是三份 `_resolve_model` 的共同语义，
批 1 合一时挂到这里。
