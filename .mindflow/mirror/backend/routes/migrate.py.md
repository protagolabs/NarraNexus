---
code_file: backend/routes/migrate.py
stub: false
last_verified: 2026-07-21
---

## Why it exists

The HTTP surface of the Agent Migration Scanner (`/api/migrate/detect`,
`/api/migrate/scan`). Turns the embedded scanner (`xyz_agent_context.migration`)
into something the Import Button (and, in desktop mode, the Migration Skill) can
call. Detect + extract ONLY — never writes to NarraNexus.

## Gotchas

- **Local-only.** `_require_local_or_raise()` returns 503 `migration_local_only`
  on cloud, because cloud's executor/backend is remote — there is no user
  filesystem to scan. The whole feature is desktop/local by nature.
- `scan` maps `FileNotFoundError` (no framework detected + no path) → 404, other
  extraction errors → 400. Extraction itself never raises (best-effort), so 400
  is rare.
- Returns the `StandardizedAgentImport.model_dump()` verbatim — the mapping/write
  is the caller's job (Migration Skill via MCP tools / Import Button via API).
