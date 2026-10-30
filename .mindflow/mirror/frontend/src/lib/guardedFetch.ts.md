---
code_file: frontend/src/lib/guardedFetch.ts
last_verified: 2026-10-30
stub: false
---

# guardedFetch.ts — the app's HTTP calls, behind the request-loop guard

## Why it exists

The request-loop guard (`requestLoopGuard.ts`) only protects calls that pass
through it. The first version sat inside `api.request`, but the artifact and
office-watch services, the system log page, blob downloads and other panels
called `fetch` directly — the same effect-driven, polling shapes as the
Channels panel whose loop got a user banned on 2026-10-30. So every HTTP call
now goes through `guardedFetch` (the plugin loader included), and
`__tests__/no-raw-fetch.test.ts` fails the build on any new raw `fetch` — called
or passed as a value — outside its allowlist.

## What it does

`guardedFetch(input, init)` has `fetch`'s signature. Reads (GET / HEAD) are
checked against the page's single `RequestLoopGuard`; a refused read logs one
console error per URL per minute (the endpoint lives only there), waits
`LOOP_BLOCK_DELAY_MS` (rejecting early on `init.signal` abort) and throws
`RequestLoopBlockedError`. Everything else is plain `fetch`, and the `Response`
comes back untouched, so callers keep their own status handling (410 / 409
branches in the artifact code, `ApiError` mapping in `api.request`).

A guarded read whose response status is in `FAILURE_STATUSES` (400 / 403 /
404 / 405) is reported to the guard (`recordFailure`), which blocks a URL that
keeps failing and pauses reads when the page fails too many in a minute. Those
are the statuses the edge monitor bans an IP on (20 a minute), so a client bug
that loops on a deleted agent's 404 or a revoked resource's 403 stays far below
that line instead of getting its user banned. The guard used to also keep a
total read budget, switched off for a known-local backend; it was removed with
the edge's volume ban (monitoring v2), so this file no longer needs to know the
runtime mode at all.

Reports name the limit that was hit and its count for the last minute (budget
reports throttle under one fixed key per budget; per-URL `loop` / `failing`
reports throttle per URL).

The page budgets are shared across same-origin tabs over a `BroadcastChannel`:
each rapid repeat and each failed read is broadcast and counted by the other
tabs (`recordRemote`). The edge counts per IP, so per-tab budgets alone would
let two tabs sum past its failure line. Without BroadcastChannel (or if
construction throws, e.g. an opaque origin) the budgets fall back to per tab.

`__resetRequestLoopGuardForTests()` rebuilds the guard and channel so test
cases cannot leak budget into each other.

## This file doesn't do

- Guard `lib/sessionGuard.ts`'s session probe — a refusal there would read as a
  dead session; it is allowlisted in the raw-fetch scan instead.
- Cover WebSocket / SSE traffic.
- **Cover plugin bundles.** The plugin loader imports them as modules in this
  page's realm, so a plugin can call the global `fetch` directly; only its
  calls through `HostAPI.http` (platform/host.ts) are guarded, and those share
  the host page's budgets. Closing this needs the loader to hand plugins a
  guarded `fetch` (or a sandbox exposing only `HostAPI.http`); patching
  `window.fetch` globally is not an option because sessionGuard must reach the
  real one.
