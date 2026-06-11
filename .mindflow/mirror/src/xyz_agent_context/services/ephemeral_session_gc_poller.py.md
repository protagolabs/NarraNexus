---
code_file: src/xyz_agent_context/services/ephemeral_session_gc_poller.py
last_verified: 2026-06-11
stub: false
---

# ephemeral_session_gc_poller.py

Background TTL cleanup for external API protocol (v0.3) ephemeral
sessions — the safety net behind the integrator's main-line cleanup
via `DELETE /v1/external/agents/{a}/sessions/{s}`.

## Why this exists

External API integrators are supposed to clean up their own sessions
via DELETE. Reality: integrators have bugs, deploys lose in-flight
cleanups, browsers close mid-stream, retries silently swallow already-
deleted 200 responses. Without a TTL safety net the `users` table
grows unboundedly.

This poller scans every agent whose owner has opted in
(`agents.external_session_ttl_seconds` is set) and cascade-deletes
every ephemeral user whose last activity exceeds the configured TTL.
Opt-in by design: NULL TTL means the owner doesn't want auto-cleanup,
and we honor that — no surprise data deletion.

## Upstream / Downstream

**Consumed by:** Nothing direct yet; intended to be started by
`backend/main.py` lifespan (Step 8.5 wiring, optional) or via the
standalone `python -m xyz_agent_context.services.ephemeral_session_gc_poller`
entry.

**Depends on:**
- `xyz_agent_context.utils.user_cascade.delete_user_cascade` — the same
  utility the DELETE endpoint uses.
- DB tables: `agents.external_session_ttl_seconds`,
  `users.owned_by_agent`, `agent_messages.updated_at`.

## Design decisions

**No system-side minimum TTL.** The Owner's v0.3 decision: if an agent
owner sets `external_session_ttl_seconds=60`, the poller honors 60s.
Documentation in the design doc warns this could delete active
conversations; we trust the operator's number rather than guard-railing
it with a minimum. Easy to add if a fleet-wide policy emerges.

**Two-stage activity check.** `last_activity` is
`MAX(agent_messages.updated_at) WHERE user_id = ?` — if the user has
ever sent or received an agent message, that's the freshness signal.
Falls back to `users.create_time` when the user was provisioned but
never produced a message (rare but real: integrator UPSERTs the user
then their server crashes mid-request). Without that fallback, every
freshly-provisioned-but-never-chatted user would be deleted
immediately.

**Best-effort, never aborts.** Per-pass failures are logged and the
loop retries on the next poll interval. A bad row in `agents` or
`users` shouldn't keep the worker from cleaning the next agent.

**Standalone entry point.** Same `python -m ...` pattern as the other
service workers (module_poller, memory_consolidation_worker). Production
deploys can either run this in its own process (preferred — failure
isolation) or start it inside backend's lifespan handler for single-
container deploys.

## Gotchas

**Activity check ignores narratives.** A session might still be "alive"
in some loose sense (the integrator may rehydrate from narrative
content) even if no new agent_messages have been written. The poller
considers narrative-only activity as no activity. In practice this
means: an idle integrator that fires DELETE only when they explicitly
sign a user out should be set to a generous TTL — long enough for any
"come back tomorrow" pattern they support — or use `permanent`
`user_type` to skip TTL entirely.

**No backoff on cascade failures.** If a particular ephemeral user
keeps failing cascade (filesystem permission issue, locked DB row),
the poller will retry it every interval. Cascade itself is idempotent
so this is safe; the noise in the log is the cost. A retry-budget per
user_id could be added later if necessary.

**Datetime parsing is bespoke.** `_parse_dt` is a tiny module-local
helper rather than a shared utility — copied from the narrative repo's
inline pattern. Sharing across modules would require putting it in
`utils/datetime.py`; not done because (a) it's 15 lines and (b) the
existing `format_for_api` / `utils.timezone.utc_now` don't share a
single parse helper either.

**TTL applies even to `external_user`.** Owners reading the code might
expect `permanent` user_type to be excluded automatically. It's NOT —
the route's `user_type` distinction only affects the initial UPSERT.
Once a user is `external_user` AND `owned_by_agent` is set AND the
agent has TTL, the poller deletes them. To exempt permanent users, the
poller could `WHERE user_type != 'external_user'`; left out of v1 to
keep behaviour aligned with "TTL means TTL, no exceptions."
