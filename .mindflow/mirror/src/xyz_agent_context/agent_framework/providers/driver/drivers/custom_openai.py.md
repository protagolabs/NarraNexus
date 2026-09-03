---
code_file: src/xyz_agent_context/agent_framework/providers/driver/drivers/custom_openai.py
last_verified: 2026-09-03
stub: false
---

# custom_openai.py — user-configured OpenAI provider

Anything ``source='user'`` + ``protocol='openai'``. Serves both
helper_llm and embedding slots from the same card row.

OpenAIConfig + EmbeddingConfig both get the card's ``api_key`` and
``base_url``. Empty base_url means "use OpenAI official defaults" —
the OpenAI SDK fills it in.

## 2026-09-03 — 末尾新增 `CONTRIBUTION`（插件平台批 0）

`CONTRIBUTION = <Driver>.contribution`：`@register` 生成的 `Contribution` 对象，被
`narranexus.kernel.plugins.builtins` 的 `builtin.providers` manifest 按符号名引用。行为不变。
