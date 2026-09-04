---
code_file: src/xyz_agent_context/agent_framework/providers/driver/drivers/openrouter.py
last_verified: 2026-09-03
stub: false
---
## 2026-09-03 — 末尾新增 `CONTRIBUTION`（插件平台批 0）

`CONTRIBUTION = <Driver>.contribution`：`@register` 生成的 `Contribution` 对象，被
`narranexus.kernel.plugins.builtins` 的 `builtin.providers` manifest 按符号名引用。行为不变。

## 2026-06-10 — build_anthropic_helper_config

Implements the new helper-slot builder for anthropic-protocol rows
(guarded the same way as build_claude_config). Lets this card serve
the helper_llm slot directly via the Messages-API helper.


# openrouter.py — OpenRouter aggregator one-key card

Same dual-row pattern as ``netmind.py`` / ``yunwu.py``.

OpenRouter's openai-protocol endpoint serves chat-completions but
**not** whisper / embeddings well (see transcription resolver — that
file deliberately skips OpenRouter for /audio). For the LLM-only
slots that this Driver handles, the standard OpenAIConfig works.

Aggregator semantics: ``supports_anthropic_server_tools=False``.
