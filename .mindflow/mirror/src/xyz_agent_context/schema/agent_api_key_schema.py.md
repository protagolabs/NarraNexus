---
code_file: src/xyz_agent_context/schema/agent_api_key_schema.py
last_verified: 2026-06-12
stub: false
---

## 2026-06-12 — fix tz-mismatch crash in is_active / status

`is_active()` and `status()` compared `expires_at` (loaded UTC-aware
from the DB) against bare `datetime.now()` (naive). As soon as ONE
key carried an `expires_at` — most commonly an old key that had been
rotated (rotate writes `grace_until = utc_now() + 7d` as
`expires_at`) — every list / status / middleware call crashed with
"can't compare offset-naive and offset-aware datetimes". The previous
edit of this md noted the risk but called it "not a real issue in
production"; the smoke run on `feat/external-api-protocol` proved
otherwise.

Fix: both methods now use `utc_now()` from `utils/timezone.py`, and a
local `_ensure_utc` helper attaches UTC tzinfo to any naive
`expires_at` that somehow slips through (defensive — the repository's
`_parse_dt` was hardened in the same commit to always return UTC-aware).

# agent_api_key_schema.py

Pydantic models for the agent_api_keys management API (v0.3 external
protocol). Separates internal DB entity (`AgentApiKey`) from API request
/ response shapes (which strip `token_hash` and add `status` for UI).

## Entity vs response separation

`AgentApiKey` is the internal representation — what the repository returns,
what business logic operates on. It includes `token_hash` (SHA256), which
the API surface must never echo back. `ApiKeyInfo` is the response-side
projection: includes `token_prefix` and computed `status`, drops the hash.
The route layer's `_entity_to_info` helper does the conversion.

## Why `status` is computed

Three states matter to the UI: active / expired / revoked. Storing a
status column in DB would mean every TTL transition needs a write — bad.
`AgentApiKey.status()` computes it on the fly from `revoked_at` and
`expires_at`. `is_active()` is the boolean form for the middleware.

## Plaintext token only on Create and Rotate

`ApiKeyCreateResponse` and `ApiKeyRotateResponse` are the ONLY two models
that carry `plaintext_token`. List / Patch / Delete / Get responses
deliberately don't have a `plaintext_token` field — the type system
prevents accidentally exposing it. `Field(description=...)` on the
plaintext_token field documents the "shown once" promise in OpenAPI
output for integrators.

## Scopes list, not bitset / enum

`scopes` is `List[str]`. Stored as JSON in DB. Three trade-offs taken:

- **Strings, not enum:** scope names should appear verbatim in error
  responses ("missing scope: session.delete") for debuggability. Python
  enum.serialise-as-name doesn't compose well with FastAPI's auto JSON.
- **List, not bitset:** future scopes are likely to grow beyond 64 (org-
  level, per-session-id, per-IP-range). A bitset would be efficient but
  fragile to add to.
- **Validation at the route layer**, not the schema. Pydantic could
  enforce the enum-string but then adding a scope would force schema
  changes; current `_VALID_SCOPES` set in the route is easier to evolve.

## Gotchas

**PATCH "absent vs null" ambiguity.** `Optional[X] = None` in
`ApiKeyUpdateRequest` can't distinguish "omit the field" from "set to
null." Treated as "untouched" in the route. To clear a field, the API
demands an explicit empty value (empty list for scopes). Adding more
clearable fields will need a sentinel value or per-field DELETE endpoint.
This is documented in the route mirror md, not just here.

**`is_active` / `status` MUST stay on `utc_now()`.** They compare
against `expires_at`, which is loaded as TZ-aware UTC. Reverting to
bare `datetime.now()` re-introduces the "can't compare offset-naive
and offset-aware datetimes" crash that took down the External API
sidebar — see the 2026-06-12 entry at the top of this md.
