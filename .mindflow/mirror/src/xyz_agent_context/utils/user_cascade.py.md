---
code_file: src/xyz_agent_context/utils/user_cascade.py
last_verified: 2026-06-11
stub: false
---

# user_cascade.py

Hard-delete a user_id and every dependent row across the schema, plus on-disk
workspace directories. The external API protocol's `DELETE
/v1/external/agents/{a}/sessions/{s}` uses this; nothing else should call it
without explicit Owner consent.

## Why this exists

`UserRepository.delete_user` is soft-delete by default (sets `status='deleted'`)
and only touches the `users` row. For external integrators that mint thousands
of ephemeral users via session IDs, we need a hard cascade that actually frees
storage — every row keyed by `user_id` in any of the 13 child tables, plus the
per-(agent, user) workspace directories on disk. Doing this as a service-level
function (rather than a UserRepository method) keeps the destructive code path
explicit and easy to audit.

## Upstream / Downstream

**Consumed by (future, not wired yet):**
- `backend/routes/external_sessions.py` — `DELETE /v1/external/agents/{a}/sessions/{s}`
- `xyz_agent_context/services/ephemeral_session_gc_poller.py` — background TTL job

**Depends on:**
- `db_backend.DatabaseBackend.delete(table, filters)` — async delete API
- `settings.base_working_path` — workspace root for the FS rmtree pass
- `schema_registry.TABLES` (indirectly, via the drift-detection invariant — see
  Gotchas)

## Design decisions

**Explicit table list, not registry introspection.** `TABLES_KEYED_BY_USER_ID`
is a hand-maintained tuple. The companion test
`test_cascade_covers_every_user_id_table_in_registry` asserts the list matches
schema_registry's actual tables with a user_id column. The first time we
introduced this design the drift test immediately caught `user_settings`
(added recently) missing from the cascade — exactly the bug it was designed
to surface. Anyone adding a new user_id-keyed table will see the test fail
until they update the list, which keeps cascade coverage honest.

**Best-effort cascade, never abort.** Each `db.delete(table, ...)` call is
wrapped in its own try/except. A failure on one table is logged as warning and
the cascade continues; the result dict reports `-1` for that table so the
caller can surface a partial-failure response (mirrors what Manyfold's
`DELETE /manyfold/agents/{id}` cascade does on origin/dev). The alternative
— abort-on-first-error — would leave the database in a worse partial-delete
state than just plowing through.

**Workspace removal AFTER DB commit.** The FS pass runs after every DB
DELETE has executed. Rationale: a leftover workspace directory is recoverable
(operator can `du -sh` and `rm -rf` later); a missing `users` row pointing at
live workspaces is not (orphan files with no DB anchor are harder to find).
Per-directory rmtree failures are logged but don't abort the rest of the
cascade.

**Workspace pattern: directory name ends with `_<user_id>`.** NarraNexus
stores per-(agent, user) workspaces at `{base_working_path}/{agent_id}_{user_id}/`
(see `skill_module.py:212`). To find every workspace belonging to one user,
scan the base dir for entries whose name ends with `_<user_id>`. This catches
all of them in one pass without needing to know every agent_id the user
touched. Non-matching directories (other users, system dirs like `bin`) are
ignored.

**Idempotent.** Deleting a `user_id` that doesn't exist returns all zeros
without raising. The DELETE statements have no FK constraints to violate;
they just match zero rows. The FS pass finds zero matching dirs.

## Gotchas

**Schema drift breaks cascade silently.** Without
`test_cascade_covers_every_user_id_table_in_registry` running in CI, a new
user_id-keyed table added to `schema_registry.py` would leave orphan rows
on every external session DELETE — and nothing would surface it. **Keep that
test green, or replace it with a runtime check inside auto_migrate's
self-heal.**

**Don't call on real users.** Hard-deleting `bin` or `local-default` would
nuke the operator's own data. The route layer that calls this MUST verify
the target user_id has `owned_by_agent IS NOT NULL` before invoking.
Convention so far: only external session DELETE calls this, and that route
only sees ephemeral user_ids by construction.

**Workspace base must exist.** If `settings.base_working_path` doesn't exist
on disk (rare — only matters in test fixtures or first boot before any agent
ran), the workspace pass quietly returns `(0, 0)`. No error.

**Logs at INFO level by default.** Every cascade emits a structured INFO log
with `user_id` and the full `cascade` dict. In high-volume external session
scenarios (Arena-style customer service) this could be noisy — consider
dropping to DEBUG once we have monitoring metrics for cascade counts.
