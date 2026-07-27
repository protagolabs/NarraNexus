---
code_file: src/xyz_agent_context/services/gateway_spend_reconciler.py
stub: false
last_verified: 2026-07-27
---

# Intent

The piece that makes the free-tier balance actually drop with agent use.

The free-tier agent runs on a proxied non-Anthropic model (the LiteLLM gateway).
The Claude Code CLI reports usage **0 for that model at every layer** — so the
agent loop's (large) token consumption never reached `cost_records` / the quota,
and `user_quotas.used` only ever moved by tiny helper-LLM amounts (the free
balance looked stuck at its initial value). This background worker closes that
gap: it reads the REAL per-request tokens the gateway itself recorded and deducts
them.

## How it works

1. `list_unmetered_revoked(grace)` → runs whose session key is revoked (run
   finished), not yet `metered_at`, and revoked longer ago than the flush grace
   (so LiteLLM's batched SpendLog writes have landed).
2. Per run: `GatewayKeyService.fetch_run_usage(key_hash)` sums the run's
   `/spend/logs` rows → `(input, output, model)`.
3. Deduct via the **same path as step_4.6**: set ContextVars
   `provider_source="system"` + `current_user_id` and call `record_cost(...,
   call_type="agent_loop")`. record_cost then writes the `cost_records` row AND
   fires the quota-deduct hook (`QuotaService.default().deduct`). ContextVars are
   reset in a `finally` so the worker task stays clean.
4. `mark_metered(run_id)` — stamped even for zero-usage/errored runs so they
   aren't re-scanned forever.

## Upstream / downstream
- Wired into `backend/main.py` lifespan via `maybe_start_gateway_spend_reconciler`
  — started only when `SYSTEM_DEFAULT_LLM_GATEWAY_URL` is set (cloud + gateway),
  a no-op locally; cancelled on shutdown. Same pattern as
  [[../agent_runtime/executor_reaper]].
- Reads/writes [[gateway_session_key_repository]] (`instance_gateway_session_keys`);
  queries the gateway via [[gateway_key_service]] `fetch_run_usage`; charges via
  `cost_tracker.record_cost` → `quota_service`.

## Design decisions / gotchas
- **Only agent runs are metered here, no double-count.** Only per-run session
  keys (`sess_*`) live in the ledger; the helper LLM uses the backend key and is
  already metered by cost_tracker inline. So this worker adds exactly the missing
  agent-loop charge.
- **Idempotent** via `metered_at`. A `fetch_run_usage` failure returns None → the
  run is left unmetered (NOT charged 0) and retried next cycle.
- **Decoupled from run timing.** It reconciles runs revoked > grace ago, so it
  doesn't race the run's final SpendLog write or depend on looking at the exact
  finish moment.
- **Never force-stops anything** (铁律 #14) — it's pure post-hoc accounting; the
  `record_cost`/deduct path is best-effort and swallows its own errors.
- `reconcile_once(db=, svc=)` takes injectable deps purely so tests drive it with
  an in-memory SQLite ledger + a mocked gateway; production resolves both from the
  db-factory singleton and env.
- The fire-and-forget task carries an `add_done_callback` that surfaces its death
  (incident lesson #2).
