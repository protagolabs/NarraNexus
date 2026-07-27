---
code_file: src/xyz_agent_context/repository/gateway_session_key_repository.py
stub: false
last_verified: 2026-07-27
---

# Intent

Data access for `instance_gateway_session_keys` — the ledger of per-run gateway
session keys. Exists so the free-tier ticket a run mints can be (a) revoked at
run end, (b) reaped when a crash orphaned it, and (c) **metered after the run
finishes** — its real token usage summed from the gateway and charged to quota
(see [[gateway_spend_reconciler]]). `run_id` is the logical key and doubles as
the gateway `key_alias`, so revocation never needs the raw secret.

## 2026-07-27 — metered_at + reconciler scan primitives

Added `metered_at` to the row mapping and two methods for the spend reconciler:
`list_unmetered_revoked(older_than_seconds)` (revoked + `metered_at IS NULL` +
`revoked_at` older than the flush grace — a raw SQL query because `get()` only
does equality, not the NULL + range predicate) and `mark_metered(run_id)`. The
age floor exists so LiteLLM's batched SpendLog writes have flushed before we sum.

## Upstream
- `GatewayKeyService` — mint/revoke caller: `create` on mint, `mark_revoked` on
  revoke, `list_active_for_user` for the executor-reaper hook.
- `GatewaySpendReconciler` — `list_unmetered_revoked` + `mark_metered`.

## Downstream
- `AsyncDatabaseClient` (`insert` / `update` / `get`).
- `schema/gateway_session_key_schema.GatewaySessionKey`.
- `parse_dt` shared helper hoisted to `repository/base.py` (was a byte-identical
  copy here + in quota_repository + artifact_repository).

## Gotchas
- We store `key_hash` (LiteLLM's non-secret token hash) for audit only — never
  the usable secret. A DB leak therefore can't be replayed against the gateway.
- `mark_revoked` is idempotent (re-revoking just refreshes `revoked_at`), which
  the reaper relies on.
- `list_active_for_user` is the crash-orphan primitive: safe to blanket-revoke
  its results ONLY because the caller (executor reaper) invokes it for users the
  admission controller reports idle — zero live loops (铁律 #14).
- `metered_at` is the idempotency guard: `list_unmetered_revoked` excludes any
  row where it's set, so a run is charged exactly once even if the reconciler
  runs repeatedly. `mark_metered` is stamped even for zero-usage (errored) runs
  so they don't get re-scanned forever.
