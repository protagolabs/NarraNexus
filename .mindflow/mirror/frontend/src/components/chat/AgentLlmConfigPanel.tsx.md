---
code_file: frontend/src/components/chat/AgentLlmConfigPanel.tsx
last_verified: 2026-07-09
stub: false
---

## 2026-07-09 — per-agent LLM config modal

Detailed per-agent editor opened from [[ComposerModelBadge]]'s ⚙. Edits both
slots for ONE agent: agent (framework + provider + model + thinking +
reasoning_effort) and helper_llm (provider + model). Each slot shows
"inheriting default" vs "custom for this agent" and a per-slot "Reset to
default" (DELETE the override). Writes via [[api]]'s setAgentLlmConfig /
resetAgentLlmConfig; changes apply on the agent's NEXT run (no hot-reload —
config is resolved per run from the DB).

Provider filtering mirrors the backend binding rules (via [[agentFramework]]):
agent slot follows the framework protocol (+codex source whitelist); helper slot
is openai/anthropic and never an OAuth provider (CLI creds can't make direct API
calls). Modeled on AwarenessPanel's per-agent modal. Uses getProviders() for the
option lists; display names fall back to raw model ids (no catalog dependency).
