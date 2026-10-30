---
code_file: frontend/src/lib/requestLoopGuard.ts
last_verified: 2026-10-30
stub: false
---

# requestLoopGuard.ts — client-side brake for runaway GET loops

## Why it exists

A React render loop (effect → fetch → setState → unstable deps → effect …)
turns one open panel into a sustained flood of identical requests. On
2026-10-30 the Channels section did that (~30 req/s); the edge flood rule then
banned the user's IP for 24h and suspended the linked account. The component
was fixed (PR #430); this module is the backstop for the next such bug, so it
degrades to one failing panel plus a console error instead of an account ban.

## What it does

`RequestLoopGuard.check(key)` returns `ok`, `loop` or `page_budget`:

- **Per URL.** Timestamps per key in a sliding window (`LOOP_WINDOW_MS` = 5s);
  past `LOOP_MAX_PER_WINDOW` (15) the key is blocked. While blocked, an attempt
  that follows the previous one within `LOOP_EXTEND_GAP_MS` (2s) pushes the
  release out to a full window from now. A serial loop that the 1s refusal delay
  slows to ~1 attempt/s therefore never drains the window and leaks nothing (a
  plain sliding window leaked ~2.5 req/s per URL — pre-push review I-1), while a
  normal poller caught by a brief burst (2s+ interval → 3s+ between attempts)
  is released when the window runs out instead of staying blocked until reload.
  A URL blocked again within `LOOP_REPEAT_WITHIN_MS` (60s) of its release is a
  repeat offender: its block doubles each time (5s, 10s, 20s, 40s, capped at
  `LOOP_MAX_BLOCK_MS` = 60s). That starves loops that refetch at once on success
  but back off on failure — they keep their attempts 2s+ apart, so they never
  extend a block, and without escalation leaked ~135 requests/min per URL. The
  release time is when the block actually ended (also for keys pruned while
  idle), so a trip long after the last one is a first offence again.
- **Per page.** At most `PAGE_GET_MAX_PER_WINDOW` (400) admitted GETs per
  `PAGE_GET_WINDOW_MS` (60s) across all URLs. Refused calls do not count, so the
  page recovers as soon as its real rate drops. This holds loops that spread
  over many URLs or change the query every time; 400 sits between the measured
  human peak (281/min per IP) and the edge ban line (562/min). It bounds only
  this page's `api.request` GETs — writes, raw `fetch`, SSE and static assets
  still count at the edge. Largest known legitimate burst: the bundle export
  page prefetches 5 GETs per selected agent, so selecting ~80 agents at once
  would exhaust the budget and those prefetches fail quietly.

`shouldReport` throttles the console error to once per key per minute (the page
budget reports under one shared key). Idle, unblocked keys are pruned once more
than 500 are tracked.

`api.request` (see `api.ts`) owns the single instance and consults it for GETs
only (`isGuardedMethod`). A refused call waits `LOOP_BLOCK_DELAY_MS` (1s;
`delayUnlessAborted` rejects early if the caller's `signal` aborts and removes its listener otherwise) and then
throws `RequestLoopBlockedError` (an `ApiError` with status 0), so a component
that retries on failure cannot spin at event-loop speed.

## Calibration

Prod access logs (2026-10-29/30, ~17h, all users except the two loop
incidents) show at most 3 identical GETs per IP in any 5s window; the fastest
legitimate poll is 2s. 15 per 5s leaves 5x headroom yet catches the observed
loop (~5 identical GETs/s per URL) within about 3s.

## This file doesn't do

- Block writes (POST/PUT/PATCH/DELETE) — refusing a save is worse than a flood.
- Cover calls that bypass `api.request` (raw `fetch`, SSE, WebSocket).
- Coordinate across tabs: each page counts alone, while the edge counts per IP.
