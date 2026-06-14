---
code_file: backend/routes/external_api.py
last_verified: 2026-06-14
stub: false
---

## 2026-06-14 — v0.5 bridged identity (`metadata.user_id`)

`_ChatMetadata` gains optional `user_id` field. `external_chat_completions`
gains 3 guards around `metadata.user_id`:

1. **scope guard** (before any DB hit): `bridge_identity` token scope
   required, else 403 `bridge_not_allowed`. Fast-fail so an untrusted
   token never costs a DB round-trip.
2. **existence guard**: user_id must exist in `users` table, else
   400 `unknown_user`.
3. **ephemeral guard**: row's `owned_by_agent` must be NULL — defends
   against an integrator passing another agent's ephemeral as if it
   were a real account (400 `not_a_real_user`).

When all three pass: skip `_ephemeral_user_id()` mint and use the
provided user_id directly as `ephemeral_user_id` (the variable name is
historical; in the bridged path it just carries the real id). Memory /
narratives / direct-chat history all live under the same user_id so
the user sees a unified view across this integration AND the main
site.

When `metadata.user_id` is absent the v0.4 ephemeral path runs
unchanged.

**DELETE behaviour for bridged sessions is naturally no-op**: the
DELETE handler computes `_ephemeral_user_id(agent_id, session_id)` and
looks it up; since the bridged path never created such a row, the
cascade returns all-zero. Real user data is therefore protected
without a special DELETE branch. See `delete_user_cascade` for the
idempotent miss behaviour.

**List sessions** filters by `owned_by_agent=agent_id` so real users
(`owned_by_agent IS NULL`) are not listed — owners discover them via
the main-site backoffice, not the external API.

# external_api.py

The `/v1/external/*` route surface for the external API protocol (v0.3).
Conditionally registered: when `ENABLE_EXTERNAL_API` is unset, the
router never gets included and the paths return FastAPI's default 404.

## Why this exists

Step 5 of the v0.3 implementation. Three reasons to split this from the
existing `openai_compat.py` and `manyfold_*` routers:

1. Manyfold's `/v1/chat/completions` (no `/external/` segment) is for the
   container-wide gateway-token model. Ours uses agent-scoped tokens.
   They must not share a path or a middleware.
2. The external protocol's auth chain (`nxk_` token) is meaningfully
   different from JWT or Manyfold gateway: it lives in its own
   middleware branch in `backend/auth.py`. Keeping its routes in a
   dedicated file makes the boundary visible.
3. Steps 6 + 7 will flesh out the chat + sessions handlers. Scaffolding
   them as 501 placeholders means integrators wiring against the URL
   shape get an actionable error instead of "no route matches."

## Upstream / Downstream

**Mounted in:** `backend/main.py` (conditional `ENABLE_EXTERNAL_API=1`).

**Auth:** `backend/auth.py` middleware (`_handle_external_api_auth`) runs
before any handler here. By the time a handler sees the request,
`request.state.external_api_authed` is True and:
  - `request.state.api_key_agent_id` is the agent the token is scoped to
  - `request.state.api_key_owner_user_id` is the agent's owner
  - `request.state.api_key_scopes` is the list of allowed scopes

**Consumed by:** external integrators (Arena 客服, etc.). Public.

## Design decisions

**Healthz is unauthenticated.** Standard pattern for readiness probes.
A K8s readiness probe with auth is a circular dependency (the probe
can't get a token, so the pod is never ready, so… ). Kept in this
router rather than the top-level `/healthz` because integrators may
have their own infra-level `/healthz` and we don't want to collide.

**Placeholders return 501 with structured detail.** Integrators
sometimes wire against an unfinished service to test their client. A
generic 404 ("no such route") wastes their debug time; a structured
501 with a per-step message tells them exactly which milestone the
route is blocked on. The detail dict shape mirrors what Step 6/7 will
return for real errors.

**The chat placeholder reads `request.state.api_key_agent_id`.** Even
though it 501s, it exercises the middleware → handler chain end-to-end,
so an integrator can sanity-check their token works before Step 6
lands.

## 2026-06-11-r2 — Step 6: chat completions handler landed

The 501 placeholder for `/v1/external/chat/completions` was replaced
with a real handler. Shape mirrors the Manyfold path
(openai_compat.chat_completions) but with three external-protocol
specifics:

1. **`metadata.session_id` is REQUIRED.** No session_id → 400
   `no_user_message`-like error (actually surfaced via Pydantic 422 if
   absent at parse time). The session_id is mapped to a per-session
   NarraNexus user_id of the form `ext_<agent-tail-8>_<sanitised
   session_id[:48]>`. The agent_id tail prevents cross-agent collisions
   when two integrators happen to pick the same session id format.
2. **Ephemeral users UPSERTed on first contact.** `_ensure_ephemeral_user`
   writes a `users` row with `owned_by_agent=<agent_id>` and
   `user_type=external_user|external_guest`. Provider config is NOT
   cloned; the lookup falls back to the agent owner via
   `UserProviderService.get_user_config`'s owned_by_agent recursion
   (same commit as Step 6 landing).
3. **`metadata.user_type` controls TTL eligibility.** "permanent" / "registered"
   → `external_user`, never auto-cleaned. Anything else (including
   missing) → `external_guest`, subject to TTL when the owner
   configures it on the agent. The default-guest choice is deliberate:
   forgetting to set user_type means the user gets cleaned up, not
   accumulated forever.

Event classification helpers are LAZILY IMPORTED from
`backend.routes.openai_compat` to avoid coupling import-time to
ENABLE_MANYFOLD_API. Both protocols' event streams come from the same
BackgroundRun, so we share `_classify_event` / `_is_error` /
`_is_terminal`.

Scope check: `chat` scope is required at the route layer via
`_require_scope`. A token issued without the `chat` scope (e.g.
session-management-only) gets 403 here.

## Gotchas

**Conditional registration means OpenAPI schema differs by env.** A
deployment with `ENABLE_EXTERNAL_API=0` won't list `/v1/external/*`
in `/docs`. Integrators reading the OpenAPI to generate clients need
to know to set the env flag first. Same pattern as `manyfold_*`.

**Scope checks aren't done in the placeholders.** When Step 6 lands,
each handler that requires a scope must check
`request.state.api_key_scopes` itself. There's no FastAPI dependency
for it yet; consider adding `Depends(_require_scope("chat"))` in Step
6 to centralise the check.

**No rate limit at this layer.** A misbehaving integrator with a valid
token can flood `/v1/external/chat/completions` until LLM quota
exhaustion. We rely on the existing quota_service for that bound, which
attributes usage to `api_key_owner_user_id`. Per-token rate limiting
is on the roadmap (v1.5) but not v1.
