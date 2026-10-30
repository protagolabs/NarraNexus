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

The total read budget is switched off only when the backend is known to be
local (`knownLocalBackend`: Tauri, forced-local config, runtime mode `local`, or
an API origin on a loopback host — `bash run.sh` in a browser), where there is
no edge ban to avoid. Forced cloud / `cloud-web` keeps it on, and so does an
unknown mode: it is fail-closed because it is the only layer that holds a loop
whose query changes on every call, and its on-switch would otherwise depend on
the deploy repo writing `mode` into `/config.js` (prod injects `mode: "cloud"`,
verified 2026-10-30). Trade-off accepted with fail-closed: a LAN self-host on a
non-loopback address keeps the total budget on even without an edge in front,
which only matters for sessions near 500 reads/min (~85+ agents).

Reports name the limit that was hit and its count for the last minute (budget
reports throttle under one fixed key per budget), so a heavy-but-legitimate
session reads as "N reads at the edge ban line", not as a phantom loop.

The page budgets are shared across same-origin tabs over a `BroadcastChannel`:
each admitted read that spends a budget is broadcast and counted by the other
tabs (`recordRemote`). The edge flood rule counts per IP, so per-tab budgets
alone would let two tabs sum past the ban line. Without BroadcastChannel (or if
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
