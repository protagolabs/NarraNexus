---
code_file: src/xyz_agent_context/schema/gateway_session_key_schema.py
stub: false
last_verified: 2026-07-23
---

# Intent

Pydantic model for one per-run gateway session key row. Deliberately minimal —
it is a revocation/reaping ledger entry, not a credential store.

## Design decisions
- **No raw secret field.** `run_id` (== gateway `key_alias`) is the revocation
  handle; `key_hash` is LiteLLM's non-secret token hash, kept for audit only.
  This is the whole point of the design: even the ledger cannot leak a usable
  key.
- Two-state status (`active` / `revoked`) — enough for "revoke at run end" +
  "reap orphans". No `expired` state because the key carries no wall-clock TTL
  (铁律 #14; validity is bounded by the run lifecycle, see [[gateway_key_service]]).

## Downstream
- `repository/gateway_session_key_repository.py` and the
  `instance_gateway_session_keys` table in `utils/schema_registry.py`.
