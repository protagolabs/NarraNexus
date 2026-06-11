---
code_file: src/xyz_agent_context/repository/agent_api_key_repository.py
last_verified: 2026-06-12
stub: false
---

## 2026-06-12 — `_parse_dt` always returns UTC-aware

Previously naive DB strings (SQLite's `YYYY-MM-DD HH:MM:SS`) parsed
to naive datetimes. The downstream `expires_at < utc_now()` in
`AgentApiKey.is_active()` then crashed with "can't compare offset-
naive and offset-aware datetimes" — visible in the External API
sidebar as soon as any token had `expires_at` set (e.g. a rotated
key with its grace `expires_at`).

`_parse_dt` now attaches `timezone.utc` to any naive result before
returning. Storage convention is UTC, so this is safe — and it
means callers can compare freely against `utc_now()` without each
of them re-implementing the normalisation.

# agent_api_key_repository.py

CRUD persistence for the `agent_api_keys` table — the storage layer behind
the external API protocol (v0.3).

## Why this exists

Two callers exist (both in this branch):

1. `backend/routes/agents_api_keys.py` — owner-facing CRUD endpoints
   (list/create/patch/delete/rotate).
2. `backend/auth.py` (Step 5) — external API middleware that O(1)-looks-up
   a token's `key_id` on every `/v1/external/*` request.

The repo centralises JSON serialisation (scopes + metadata), datetime
coercion, and the soft-delete-by-`revoked_at` convention so the route and
middleware layers don't repeat that logic.

## Upstream / Downstream

**Consumed by:** routes/agents_api_keys.py, future external API middleware.
**Depends on:** `db_factory.get_db_client` (caller-injected `db`),
`schema/agent_api_key_schema.AgentApiKey` for entity shape.

## Design decisions

**Single `get_by_key_id` lookup, no broader query.** The middleware path
needs the row by the short `key_id` (extracted from the plaintext token).
No `find_by_token_hash` API — that would imply scanning, and the only
correct path is "extract key_id → row → constant-time SHA256 compare on
returned `token_hash`". `list_for_agent` exists for the UI; nothing else
should iterate the table.

**Soft-delete (revoked_at), never DROP.** Keys revoked or rotated keep
their row so the UI can show audit history (last_used_at, name, revoked_at,
metadata). `list_for_agent(include_revoked=True)` is the default for the
UI; the middleware only treats `revoked_at IS NULL` rows as valid.

**`touch_last_used` is silent on failure.** Logged at WARNING but never
raises. The chat hot path must NEVER stall on a DB write. The data is
purely advisory (UI "last used X minutes ago" hint); losing it has no
correctness impact. Run on a fire-and-forget asyncio task in the
middleware to avoid even the round-trip latency.

**Rotate is "insert + update old expiry," not "swap in place."** The route
layer's `/rotate` endpoint calls `insert(...)` to mint a new row, then
`update(key_id, {expires_at: now + grace})` on the old one. Old row still
appears in the list view with status "active (rotated)" during grace,
then transitions to "expired" once `expires_at` passes. Integrators get a
deploy window to migrate without downtime.

## Gotchas

**JSON columns are TEXT in SQLite.** scopes and metadata go through
`json.dumps` on write and `json.loads` on read. If a row is corrupted
(manual SQL edit, partial commit) the `_row_to_entity` parser falls back
to empty list / None rather than raising — corruption shouldn't 500 the
whole list endpoint. Risk: a malformed scopes row would silently grant
zero permissions; an audit log on parse failure is worth adding once
this is in production.

**get_by_key_id returns None on miss, not raises.** The middleware treats
None as "401 invalid_token" without try/except. Don't "fix" it to raise
404 — that changes the trust boundary.

**Datetime coercion handles three formats AND always returns UTC-aware.**
ISO 8601 with timezone, ISO 8601 without timezone, and
`YYYY-MM-DD HH:MM:SS` (SQLite's `datetime('now')` default). Anything
else → None. Naive results get `tzinfo=timezone.utc` attached before
return — storage convention is UTC, so this is the right default and
it protects downstream comparisons against the offset-naive crash.
