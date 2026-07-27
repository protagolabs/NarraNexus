---
code_file: src/xyz_agent_context/agent_framework/providers/gateway_key_service.py
stub: false
last_verified: 2026-07-27
---

# Intent

Mint / revoke the per-run LiteLLM gateway **session keys** ("会话票") that back
the free tier — and, since 2026-07-27, **read back** each run's real usage. The point of the whole design: the Power/Netmind **master key
never enters a process that runs user-controlled agent logic**. It lives only in
the LiteLLM gateway container; for each run the backend (`open_backend_session`,
called from `step_3_agent_loop`) asks the gateway for a per-run key scoped to one
user, injects that ticket into the `ClaudeConfig` ContextVar, and it rides
`provider_configs` to the executor. The ticket only works against our gateway,
is revocable, and — when `key_max_budget_usd` is set — carries a hard per-key USD
ceiling the gateway enforces, so a leaked ticket's blast radius is genuinely
bounded rather than merely "the master key hidden".

## Upstream
- `step_3_agent_loop` via `open_backend_session()` — the BACKEND mints before
  dispatching the driver and revokes (`BackendGatewaySession.close()`) in the
  finally. The mint MUST be here, not in `xyz_claude_agent_sdk.agent_loop`: that
  runs in the executor (user-controlled code, no `provider_source`, must never
  hold the admin key). `open_backend_session` injects the ticket into the
  `ClaudeConfig` ContextVar so it rides `provider_configs` to the executor.
- `executor_reaper` post-reap hook — `revoke_all_for_user` when an idle user's
  executor is culled (crash-orphan cleanup).

- `GatewaySpendReconciler` — calls `fetch_run_usage(key_hash)` after a run ends.

## Downstream
- LiteLLM proxy admin API: `POST /key/generate`, `POST /key/delete`, and
  `GET /spend/logs?api_key=<hash>` (httpx, bearer = the gateway admin key).
- `GatewaySessionKeyRepository` — the `instance_gateway_session_keys` ledger.

## 2026-07-27 — fetch_run_usage (the authoritative token source)

`fetch_run_usage(key_hash)` GETs `/spend/logs?api_key=<hash>` and sums
`prompt_tokens` / `completion_tokens` across the run's rows, returning
`(input, output, model)` — or **None on any failure or empty key_hash** (the
reconciler must NOT mark a run metered on None; it retries next cycle). This is
the authoritative token source for a proxied agent run: the Claude CLI reports
usage 0 for non-Anthropic models at every layer (`ResultMessage.usage`,
`message_start.message.usage`), but the gateway sits in the request path and
records real per-request tokens. `/spend/logs?api_key=` (granular per-request
rows) is the working endpoint — the `start_date`/`end_date` form only returns
daily aggregates with spend 0.

## Design decisions / gotchas
- **No wall-clock TTL** on minted keys (`duration` omitted). 铁律 #14: runs can
  last hours and the CLI reads the token once at spawn — a timed key would
  guillotine long runs. Validity is bounded by the run (revoke on finally) and,
  for crash orphans, by the executor-reaper hook — not by a timer.
- **Per-key `max_budget`** (USD, from `SYSTEM_DEFAULT_LLM_GATEWAY_KEY_MAX_BUDGET_USD`)
  is the ONLY thing that actually bounds a leaked ticket: our `cost_tracker` only
  meters calls routed through the backend, so without it a ticket used directly
  against the gateway is uncapped (and non-expiring for the run). Omitted when
  unset/≤0. There is deliberately **no crash-orphan sweep API** — that lived as
  dead code (no prod caller) and was removed; orphan cleanup is the reaper hook.
- **Revoke by `key_alias == run_id`**, so the raw secret is never persisted. The
  ledger stores only the non-secret token hash + alias.
- **Never raises into the agent loop, never falls back to the master key.** Mint
  failure → `None` (caller surfaces `gateway_unavailable`, a recoverable error).
  Revoke is best-effort cleanup that never raises.
- **Ledger row written BEFORE the key is handed out**; if that write fails the
  key is immediately deleted, so we never emit an untracked (unreapable) key.
- `from_env` returns `None` when the gateway isn't configured — the SAME url +
  admin env that gates `SystemProviderService`, so the two agree. The enable
  check must stay in sync with that service.
- `transport` ctor arg exists purely so tests drive it with `httpx.MockTransport`.
