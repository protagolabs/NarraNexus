---
code_file: frontend/src/lib/agentFramework.ts
last_verified: 2026-07-09
stub: false
---

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
