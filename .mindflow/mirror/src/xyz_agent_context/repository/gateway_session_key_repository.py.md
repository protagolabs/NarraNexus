---
code_file: src/xyz_agent_context/repository/gateway_session_key_repository.py
stub: false
last_verified: 2026-07-24
---

# Intent

Data access for `instance_gateway_session_keys` — the ledger of per-run gateway
session keys. Exists so the free-tier ticket a run mints can be (a) revoked at
run end and (b) reaped when a crash orphaned it. `run_id` is the logical key and
doubles as the gateway `key_alias`, so revocation never needs the raw secret.

## Upstream
- `GatewayKeyService` — the only caller: `create` on mint, `mark_revoked` on
  revoke, `list_active_for_user` for the executor-reaper hook.

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
