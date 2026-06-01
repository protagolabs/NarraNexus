---
code_file: src/xyz_agent_context/agent_framework/provider_driver/resolver.py
last_verified: 2026-05-31
stub: false
---

# resolver.py — single-point LLM config resolution

## Why this exists

The codebase had two parallel resolve paths — ``ProviderResolver`` for
HTTP middleware and ``api_config._get_user_llm_configs_strict`` for
background triggers. Each had its own completeness check and error
handling, drift between them caused real bugs (Xiong's case shipped
because the frontend Settings check ran the HTTP path while the LarkTrigger
that broke ran the background path). This module collapses both into one
function and ``api_config._get_user_llm_configs_strict`` now delegates
to it. ``ProviderResolver.resolve_and_set`` will follow in Phase 2.

As of 2026-05-31, the runtime path also resolves Codex agent config.
When the agent slot row has ``agent_framework='codex_cli'``, the agent
slot is interpreted as an OpenAI-protocol Codex provider and produces
``CodexConfig`` in the returned ``RuntimeLLMConfigs`` bundle. The legacy
``resolve_user_llm_configs`` wrapper still exposes the old three-config
tuple for non-agent-loop callers.

Codex OAuth rows are canonicalized at resolve time: any
``source='codex_oauth'`` / ``auth_type='oauth'`` card uses the Codex CLI
auth reference, even if stale local data still carries a Claude CLI
``auth_ref`` from an older build. This keeps agent-loop auth tied to
``~/.codex/auth.json`` without requiring users to recreate the provider.

## Pipeline

```text
user_id
  └─ db.get('user_slots', user_id=user_id)  → 3 rows expected
       └─ for each slot:
            db.get_one('user_providers', provider_id=...)
              ├─ visibility check (owner_user_id matches OR is null)
              ├─ self_heal_if_broken (rewrites slot.model if needed)
              ├─ on-the-fly driver_type derive if backfill hasn't run yet
              ├─ DRIVER_REGISTRY[driver_type] → Driver instance
              └─ driver.build_<kind>_config(slot.model)
                 OR CodexConfig for agent_framework='codex_cli'
```

## Visibility rule

A card is visible if ``owner_user_id == user_id`` OR
``owner_user_id IS NULL``. The null case covers two things at once:

* Cloud system-shared cards (admin created the row with null owner).
* Legacy rows that pre-date the Phase 0 ``owner_user_id`` column —
  for those we fall back to ``card.user_id == user_id`` so the
  resolver doesn't refuse old data on first boot.

## Errors

Every failure raises ``LLMConfigNotConfigured`` with an actionable
message. The caller's UX layer surfaces it to the user. No silent
fallback to a different account — that was a leading cause of
billing surprises in the old code.
