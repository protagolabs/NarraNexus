---
code_file: src/xyz_agent_context/module/narramessenger_module/_narramessenger_credential_manager.py
stub: false
last_verified: 2026-08-11
---

## 2026-08-11 — two reverse lookups for the prewarm endpoint

`get_by_matrix_user_id` and `get_by_profile_id` were added. Both are needed
by a follow-up "prewarm" HTTP endpoint: NarraMessenger calls in with the
agent's Matrix identity (`@agent-…:homeserver`) today, and — in future —
its own `agent_profile_id`. Until now every query filtered by `agent_id`
only, so there was no way to resolve a credential row from either of
those. Both follow the existing `get()` shape (empty-string guard, decode
via `_row_to_cred`, `None` on miss); `get_by_matrix_user_id` relies on the
pre-existing UNIQUE index to guarantee at most one row, `get_by_profile_id`
does not (see caveat below).

**Caveat — `nexus_profile_id` back-fill gap**: the column existed in the
schema since inception but `do_bind` never wrote it (see
`_narramessenger_service.md` 2026-08-11 entry) — every row bound before
this change has `nexus_profile_id == ""`. `get_by_profile_id` only
resolves rows bound (or rebound) after 2026-08-11; older agents need a
rebind before the prewarm endpoint can find them by profile id. A
non-unique index (`idx_narramessenger_profile`, see `schema_registry.py`)
backs the lookup — non-unique because empty-string rows would otherwise
collide on a unique index, and profile-id uniqueness isn't a contract
we've verified against NarraMessenger's platform semantics.

## 2026-07-03 (review fix) — dead `update_matrix_credentials` deleted

PR #60 review #5. The method was never called anywhere — the real bind path
writes creds via `upsert(...)`. Keeping a second, divergent token-write path
risked silent drift (铁律 #8/#5), so it's deleted. `upsert` is the single
credential write path; `update_since_token` / `update_device_id` remain the
narrow single-column updaters.

## 2026-07-02 (Commit 7) — `list_active_by_mode` removed

Direct Matrix is the only transport; there is no second trigger to
disambiguate credential rows for. `list_active_by_mode(connection_mode)`
is gone; MatrixTrigger's `load_active_credentials()` now calls
`list_active()` directly. The `connection_mode` column stays in the
schema for existing rows (see [[schema_registry.py]] `channel_narramessenger_credentials`
block); the composite `(connection_mode, enabled)` index becomes dead
weight but is left in place — dropping the index requires a manual
migration and the extra bytes per row are negligible.

Pre-Matrix rows without a `matrix_access_token` load through
`list_active()`, then MatrixTrigger.connect raises `ValueError` on the
missing token → base flips `enabled=False` → owner must re-bind. This
is by design: silently upgrading a Gateway row would need a Matrix
access token we don't have, and asking the owner to re-bind is the
honest recovery path.

## Why it exists

CRUD for `channel_narramessenger_credentials` (one row per agent). Dataclass
`NarramessengerCredential` + `NarramessengerCredentialManager`, mirroring the
telegram credential manager.

## Design decisions

- **One secret only: `bearer_token`** (base64-encoded in DB, NOT encryption —
  same placeholder convention as lark/slack/telegram). v1 needs no Matrix
  access token because there is no Matrix client.
- **Fields beyond the token**: `backend_base_url` + `matrix_homeserver_url`
  (the two URLs), `matrix_user_id` (bot identity, unique), `nexus_principal_id`
  / `nexus_profile_id` (ids returned at connect), `bind_room_id`,
  `owner_matrix_user_id` / `owner_name` (trust signal), `connection_mode`
  (default `gateway`), `enabled`.
- **`upsert` writes the row directly** (no `getMe`-style validation API like
  Telegram); liveness is checked at runtime via `/status`.
- **`list_active()`** (`enabled=1`) is what the trigger watcher consumes;
  `set_enabled(False)` lets the trigger break a reconnect loop against a
  revoked bearer.
- `to_public_dict()` never includes the bearer token.

## Upstream / downstream

- **Used by**: the trigger (`list_active`, and — since 2026-07-02 —
  `update_owner` from `NarramessengerTrigger._maybe_claim_owner`, the X2/X3
  owner-auto-claim fix), the module (`get`), the MCP tools, and
  `scripts/debug/seed_narramessenger_credential.py` (`upsert`).
- **Table**: `channel_narramessenger_credentials` (see `utils/db/schema_registry.py`).

## Gotchas

- `matrix_user_id` is UNIQUE — the same Matrix bot binds to at most one agent
  (two agents polling the same bearer would split invocations).
