---
code_file: backend/routes/channels/narramessenger.py
stub: false
last_verified: 2026-08-11
---

> 2026-08-10:`_verify_agent_ownership` 不再是本文件定义——模块级别名指向
> `backend/routes/_ownership.py::check_owned`(canonical;DB 故障走 503 而非 200)。

## 2026-08-11 — sandbox prewarm endpoints (F28 voice)

`POST /prewarm` + `GET /prewarm/status`: the NarraMessenger backend calls
these when a voice call starts ringing, so the owner's executor container is
warm before the call connects (cold start costs up to tens of seconds behind
the "connecting" UI). Contract field names are FROZEN — already published to
the partner.

- **Auth is the per-agent `bearer_token`**, NOT a user JWT: the caller is a
  machine with no session. Both paths sit in `AUTH_EXEMPT_PATHS` and
  self-credential in-handler via `hmac.compare_digest` (timing-safe), same
  pattern as `/api/admin/runtime/status`. Ordering: identifier misuse → 422,
  missing bearer → 401 (BEFORE any db work), then the row lookup (needed to
  obtain the expected token) → 404 unknown/disabled, mismatch → 403.
- **Executors are per-USER**: resolve `agent_id -> owner` through
  `AgentRepository.resolve_owner`, honoring its ""/None split (None = lookup
  failed → 503; "" = unknown agent → 404).
- `agent_profile_id` is the reserved secondary key — resolves only for rows
  bound after profileId persistence (32c94c85); older bindings need a rebind.
- **`_PREWARM_STATE` is an in-process ledger** (user_id → status/url/ts).
  Single-host by design today (binding rule #20): the durable seam is the
  broker itself — `ensure_executor` is idempotent, so a backend restart just
  re-reports `ready: false` and the next prewarm re-ensures. No broker
  configured (local/desktop) → 202 `"skipped"`, never an error.
- The warmer is a fire-and-forget task that catches ALL its own exceptions
  (engineering lesson #2); prewarm failure must never block the call itself.

## Why it exists

The frontend "paste the bind link" entry point for NarraMessenger:
`GET /api/narramessenger/credential`, `POST /bind`, `POST /unbind`. Mirrors
`backend/routes/channels/lark.py` (same `_verify_agent_ownership` local-vs-cloud pattern).

## Design decisions

- **All real work lives in `_narramessenger_service.do_bind` / `do_unbind`** —
  shared with the `narra_bind` MCP tool, so the chat path and the dashboard path
  bind identically. The route is a thin auth + validation wrapper.
- `/credential` returns the sanitised `get_public()` view (NO bearer token);
  `data` is null when unbound — which is what `IMChannelsSection.fetchConnected`
  keys on for the ✓/not-bound badge.
- Registered in `backend/main.py` under `/api/narramessenger`.
