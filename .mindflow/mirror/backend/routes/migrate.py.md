---
code_file: backend/routes/migrate.py
stub: false
last_verified: 2026-07-21
---

## Why it exists

The HTTP surface of Agent Migration. `/detect` + `/scan` are the read side
(detect + extract other-framework configs into standardized JSON); `/apply` is
the write side (map → `apply_plan` → a populated NarraNexus agent).

## Gotchas

- **`/detect` + `/scan` are local-only.** `_require_local_or_raise()` returns 503
  `migration_local_only` on cloud, because cloud's executor/backend is remote —
  there is no user filesystem to scan. Those two endpoints are desktop/local by
  nature.
- **`/apply` is NOT local-gated** — it writes to NarraNexus and works wherever
  the backend runs. It builds the plan (`build_plan`) and executes it
  (`apply_plan`), with `user_id` from `resolve_current_user_id`. Local-skill
  file-copy only fully succeeds when the backend is on the same machine as the
  source; otherwise it degrades to marketplace-install / unmatched.
- `scan` maps `FileNotFoundError` (no framework detected + no path) → 404, other
  extraction errors → 400. Extraction itself never raises (best-effort), so 400
  is rare.
- `/scan` returns `StandardizedAgentImport.model_dump()` verbatim so the caller
  (or user) can edit before POSTing it back to `/apply`.
