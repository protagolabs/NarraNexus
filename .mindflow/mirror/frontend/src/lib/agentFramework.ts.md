---
code_file: frontend/src/lib/agentFramework.ts
last_verified: 2026-07-09
stub: false
---

## 2026-07-09 — defaultHelperModel(选 helper provider 时默认便宜模型)

新增 `defaultHelperModel(source, protocol, modelIds)`:helper slot 选定 provider 后,
默认挑**推荐的便宜模型**而非 `models[0]`(旗舰)。优先 `RECOMMENDED_HELPER_MODEL_BY_PROTOCOL`
(openai→gpt-5.4-mini / anthropic→claude-haiku-4-5);OAuth provider 列的是 CLI 别名、
具体推荐 id 可能不在列表里,故映射到后端 auto-bind 同款别名(claude_oauth→`haiku`、
codex_oauth→`gpt-5.4-mini`),都不在时才回退首个。`ModelDefaultsSettings` 与
`AgentLlmConfigPanel` 共用,修掉"选 codex 后 helper 默认成 gpt-5.5 旗舰 / 选 claude 后默认
opus"的问题。

## 2026-07-09 — shared framework/model helpers for the provider UI

Single source of truth for the LLM provider/slot UI, shared by the user-level
Settings editor ([[ProviderSettings]]) and the per-agent chat surfaces
([[ComposerModelBadge]], [[AgentLlmConfigPanel]]) so a per-agent override offers
exactly the same choices as the global-default editor.

Holds: ``AGENT_FRAMEWORKS`` + ``isCodexFramework``; ``CODEX_CURATED_MODELS`` +
``CODEX_ALLOWED_PROVIDER_SOURCES`` (must mirror backend
``user_provider_service`` — codex CLI only speaks the Responses API, so
aggregator sources are excluded); ``RECOMMENDED_HELPER_MODEL_BY_PROTOCOL``
(mirror of backend ``_ONBOARD_HELPER_MODELS``); ``MODEL_SUGGESTION_GROUPS``;
reasoning option lists; and ``getModelsForSlot(prov, slot, framework,
knownModels)`` (agent+codex → curated set, else the provider's own models).
These were previously local to ProviderSettings; extracting them avoided
duplicating the codex rules across the new per-agent components.
