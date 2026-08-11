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
  bound after profileId persistence landed (2026-08-11); older bindings need
  a rebind. Its `max_length=64` matches the `nexus_profile_id` VARCHAR(64)
  column: longer values can never resolve, so they 422 instead of 404.
- **`_PREWARM_STATE` is an in-process ledger** (user_id →
  status/url/gen/task). Single-host by design today (binding rule #20): the
  durable seam is the broker itself — `ensure_executor` is idempotent, so a
  backend restart just re-reports `ready: false` and the next prewarm
  re-ensures. No broker configured (local/desktop) → 202 `"skipped"`, never
  an error.
- **In-flight dedup**: a POST that finds a live `"warming"` entry (task not
  done) answers 202 `"warming"` WITHOUT spawning another task — the partner
  may POST several times per ring, and piling ensure calls onto the broker
  helps nobody. `"failed"`/dead entries fall through, so retries never wedge.
- **Generation guard**: each warmer task carries a `gen` from
  `_PREWARM_GEN`; every ledger write inside `_do_prewarm` (ready, failed,
  broker-vanished pop) fires only if the entry's gen is still its own, so a
  stale task can never clobber a newer entry. `_do_prewarm` mutates the
  entry in place (never replaces it) — the ledger entry IS the task's strong
  reference (the event loop only keeps weak refs; the old `_PREWARM_TASKS`
  set is gone). The dedup check means the route only ever replaces an entry
  whose task is already done (live warming entries return early), so no
  running task loses its ref; even a hypothetically superseded task would
  just finish with its writes no-oping on the gen guard.
- The route stores the ledger entry BEFORE `create_task` and patches
  `entry["task"]` in with no await in between — the one ordering where
  neither the new task nor a concurrent request can observe a half-built
  entry (see the inline comment).
- The warmer is a fire-and-forget task that catches ALL its own exceptions
  (engineering lesson #2); prewarm failure must never block the call itself.
- Both liveness probes (POST already-warm check and `/prewarm/status`) use
  `timeout=1.0`: the caller is mid-ring; a wedged container must not cost
  the full 5s default.

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
