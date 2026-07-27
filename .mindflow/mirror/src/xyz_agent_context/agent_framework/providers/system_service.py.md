---
code_file: src/xyz_agent_context/agent_framework/providers/system_service.py
stub: false
last_verified: 2026-07-27
---

# Intent

Module-level singleton that reads `SYSTEM_DEFAULT_LLM_*` env vars once at
first `instance()` call and exposes a fixed, cloud-only `LLMConfig` for the
free tier.

**Gateway mode (2026-07).** The backend NO LONGER holds the upstream master
key. The free tier runs through a LiteLLM gateway container that alone holds
the real key. So the exposed `LLMConfig` points BOTH protocol slots at the
gateway; the agent (Anthropic) slot carries an EMPTY `api_key` placeholder — the
real per-run session key is minted on the BACKEND by
`gateway_key_service.open_backend_session` (called from `step_3_agent_loop`, NOT
in the executor) and injected into the `ClaudeConfig` ContextVar so it rides
`provider_configs` to the executor — and the helper (OpenAI) slot carries a
backend-resident gateway key (a bounded virtual key, not the master). This is
the "don't leave a durable master key where user-controlled agent code can read
it" fix; see [[gateway_key_service]] and [[step_3_agent_loop]].

## Upstream
- ProviderResolver — calls `is_enabled()` as branch-A short-circuit, then
  `get_config()` to inject the system LLMConfig into the request's
  ContextVar when the user has no personal provider and has budget.
- QuotaService.init_for_user — calls `is_enabled()` to decide whether to
  seed a quota row, and `get_initial_quota()` to read the initial token
  counts the new row is stamped with.
- App lifespan — calls `instance()` once at startup so env reads happen
  on a controlled thread, not mid-request.

## Downstream
- `schema/provider_schema.py` — LLMConfig / ProviderConfig / SlotConfig
  / ProviderSource / ProviderProtocol / AuthType

## Gating rules (all must hold for is_enabled() == True)
1. Cloud mode (`DATABASE_URL` non-sqlite OR `DB_HOST` set)
2. `SYSTEM_DEFAULT_LLM_ENABLED=true` (case-insensitive)
3. `SYSTEM_DEFAULT_LLM_GATEWAY_URL` AND `SYSTEM_DEFAULT_LLM_GATEWAY_ADMIN_KEY`
   both non-empty (gateway mode — replaces the old raw-`API_KEY` gate)
4. `SYSTEM_DEFAULT_LLM_AGENT_MODEL` and `_HELPER_MODEL` present and non-empty
5. `SYSTEM_DEFAULT_LLM_SOURCE` parses as a ProviderSource enum value

`SYSTEM_DEFAULT_LLM_GATEWAY_BACKEND_KEY` is OPTIONAL and does NOT gate: absent
→ the helper slot has no key and degrades, but the security-critical agent slot
(per-run minted) is unaffected. The `GatewayKeyService.from_env` enable check
reads the SAME url+admin env, so the two must agree.

Any failure leaves `_enabled=False` and `_config=None`; `get_config()`
will raise, which is intentional — callers should guard on
`is_enabled()` first.

## Design decisions
- Two ProviderConfig entries sharing `linked_group="system_default"` capture the
  "one gateway, two protocols" shape. They no longer share a key: the agent
  (Anthropic/Bearer) slot's `api_key` is `""` (per-run session key injected at
  spawn), the helper (OpenAI/api_key) slot's is the backend gateway key. Both
  `base_url`s point at the gateway (per-protocol env, defaulting to GATEWAY_URL).
- `supports_anthropic_server_tools=False` on the anthropic provider —
  NetMind proxies but does not execute server-side tools like
  `web_search_20250305`; the tool-policy guard layer uses this flag.
- Singleton with `_instance` class-level cache. The autouse fixture in
  `tests/agent_framework/test_system_provider_service.py` resets this
  between tests so env changes are observed.
- `get_initial_quota()` is callable even when disabled. Reading quota
  env vars costs nothing and the function returns `(0, 0)` by default,
  which is the safe value for disabled systems.

## Gotchas
- Changing any `SYSTEM_DEFAULT_LLM_*` env requires restarting the backend
  process. There is no hot reload by design — a mid-request env change
  would create a config inconsistency window.
- The cloud-mode check must stay in sync with `backend/auth.py`'s
  `_is_cloud_mode()`. Both read `DATABASE_URL` and `DB_HOST` identically.
  Diverging them will create split-brain behaviour where registration
  seeds quota but the resolver does not route system traffic (or vice
  versa).
