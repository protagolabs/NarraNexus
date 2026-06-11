---
code_file: backend/routes/external_api.py
last_verified: 2026-06-11
stub: false
---

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
