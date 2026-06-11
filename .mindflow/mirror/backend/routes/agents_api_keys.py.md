---
code_file: backend/routes/agents_api_keys.py
last_verified: 2026-06-11
stub: false
---

# agents_api_keys.py

Owner-facing CRUD for `nxk_` API tokens. Five endpoints under
`/api/agents/{agent_id}/api-keys`:

- GET    /                    → list (revoked rows included w/ status)
- POST   /                    → create (returns plaintext ONCE)
- PATCH  /{key_id}            → rename / scopes / expiry / metadata
- DELETE /{key_id}            → soft revoke (idempotent)
- POST   /{key_id}/rotate     → revoke old w/ grace + create new

All endpoints require JWT auth (the caller's own session) and a
`created_by(agent_id) == current_user_id` check before any operation.
Trying to manage another user's agent tokens → 403.

## Why this exists

The external API protocol's auth model (v0.3) needs to let the agent
owner (a) mint tokens, (b) see what they've minted, (c) rename/scope-
adjust/expire them, (d) revoke, (e) rotate to swap secrets without
downtime. All of these are administrative — the actual token usage
runs through the `/v1/external/*` middleware (Step 5/6), not through
here. Splitting "manage tokens" from "use tokens" gives clean separation:
the management surface is JWT-authed (the owner clicks buttons in the
UI), the usage surface is `nxk_`-authed (external integrator's
server-to-server calls).

## Upstream / Downstream

**Wired in:** `backend/routes/agents.py` aggregator (registered after
mcps/cost).

**Consumed by:** the agent-detail-page "API 接入" tab (Step 9 frontend).

**Depends on:**
- `backend.auth.resolve_current_user_id` — JWT identity.
- `repository.AgentRepository.get_agent` — owner check.
- `repository.AgentApiKeyRepository` — actual persistence.
- `utils.api_key_token.mint_token` — generates `nxk_apk_…` plaintext.

## Design decisions

**Owner check via 403 on every endpoint.** `_resolve_agent_owner_or_403`
runs first thing on every handler. We deliberately do NOT use FastAPI
dependency injection for it — being inline makes the security check
visible at every endpoint and harder to accidentally remove via a
refactor.

**Plaintext shown exactly once.** The response from POST / and
POST /rotate is the ONLY place the plaintext token appears server-side
after creation. The frontend MUST surface it in a copy-and-save modal.
Subsequent GET calls have no way to recover it — by design (the DB has
only the SHA256).

**Rotate sets 7-day grace on the old key.** `ROTATE_GRACE_DAYS = 7`
matches GitHub PAT rotation. The old token still authenticates for 7
days; after that, the middleware's expires_at check kicks in and the
old token returns 401. This gives integrators a deploy window to
update their secrets without downtime.

**Scope validation at the route, not deeper.** The four valid scope
strings are enumerated as `_VALID_SCOPES`; bogus strings are rejected
with 422 before they hit the DB. This prevents silent dead-letter
scopes (a typoed scope that never matches anything in the middleware,
giving the appearance of working then failing in production).

**Soft revoke is idempotent.** Calling DELETE on an already-revoked key
returns 200 with the existing revoked row, not 4xx. Manual operator
remediation might re-revoke the same key out of caution; that should
"just work."

**409 on rotating a revoked key.** You can't rotate a dead key — that
would silently re-enable it (rotate updates old's `expires_at`, which
on a revoked key has no defined semantics). Force the caller to use
POST / (fresh create) instead.

## Gotchas

**Owner check leaks existence to logged-in users.** Currently we return
403 "not the owner" when a logged-in user hits another user's agent's
key endpoint. That tells them the agent_id exists. The Manyfold convention
(404 for "not yours" too) is stricter; we don't follow it here because
the threat model on this surface is owner-vs-owner enumeration, and the
existence leak is judged acceptable. Switch to 404 if multi-tenant
enumeration becomes a concern.

**PATCH semantics: absent vs null.** Pydantic's `Optional[X] = None`
makes "absent in JSON" and "explicitly null" indistinguishable. We treat
both as "untouched" for the PATCH semantics. To clear a field you can't
PATCH it to None — currently the only clearable field is scopes (empty
list = clear). Adding more clearable fields will require a sentinel
("__unset__" string or similar) or a separate DELETE endpoint per field.

**No rate limit on creation.** A logged-in owner can mint as many keys
as their patience allows. The DB's UNIQUE constraint on `key_id` will
gracefully reject collisions (and the route currently doesn't retry —
it will surface a 500). At 48-bit randomness, collision probability
per key is ~2^-48; per million keys ~2^-28. Realistic concern level:
negligible until we have someone scripting key creation in a loop.

**Rotate doesn't preserve last_used_at on the new key.** The new key's
last_used_at starts NULL — which is correct semantically (it's a fresh
secret with no usage history), but distinct from "rotated but used yet
once." Combined with the old key's history-preserving soft-delete, the
audit chain is intact across the rotate.
