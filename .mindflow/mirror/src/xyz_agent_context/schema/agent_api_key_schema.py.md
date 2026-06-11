---
code_file: src/xyz_agent_context/schema/agent_api_key_schema.py
last_verified: 2026-06-11
stub: false
---

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

**`is_active` reads `datetime.now()` naive.** `expires_at` stored in DB
is timezone-aware (UTC) per the schema_registry comment, but `is_active`
calls `datetime.now()` without tz. If a test injects a naive `expires_at`
the comparison raises TypeError. Not a real issue in production (DB
always returns UTC) but if you're seeing weird tz behaviour, normalise
both sides to UTC explicitly.
