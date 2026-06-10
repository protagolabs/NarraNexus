---
code_file: src/xyz_agent_context/agent_framework/helper_sdk.py
last_verified: 2026-06-10
stub: false
---
# helper_sdk.py — protocol-agnostic helper_llm factory

## Why it exists

Single dispatch point for every helper_llm call site (entity extraction,
narrative update/judge, job lifecycle, memory consolidation, chat
fallback reply, instance decision). Before 2026-06-10 those ~15 sites
constructed `OpenAIAgentsSDK()` directly, hard-binding the helper to the
OpenAI protocol; the factory makes the helper swappable per iron rule #9
and is what lets a single Claude key serve both slots.

## How dispatch works

`get_helper_sdk()` reads the CURRENT asyncio task's
`_anthropic_helper_ctx` ContextVar (set by `set_user_config` from
`RuntimeLLMConfigs.anthropic_helper`):

- ctx set → `AnthropicHelperSDK` (Messages API)
- ctx None → `OpenAIAgentsSDK` (Chat Completions) — the default and the
  unchanged path for every existing OpenAI-helper user

Imports are lazy inside the function to avoid a circular import at
module load (both SDK modules import api_config).

## Gotchas

- New helper call sites MUST go through this factory — grep gate:
  `OpenAIAgentsSDK(` should not appear outside openai_agents_sdk.py /
  helper_sdk.py / tests.
- Dispatch is per-task and resets on every `set_user_config` call
  (passing no anthropic_helper clears the ctx), so multi-tenant
  concurrent turns cannot leak each other's helper choice.
