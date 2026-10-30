---
code_file: frontend/src/lib/requestLoopGuard.ts
last_verified: 2026-10-30
stub: false
---

# requestLoopGuard.ts — client-side brake for runaway read loops

## Why it exists

A React render loop (effect → fetch → setState → unstable deps → effect …)
turns one open panel into a sustained flood of requests. On 2026-10-30 the
Channels section did that (~30 req/s); the edge flood rule then banned the
user's IP for 24h and suspended the linked account. The component was fixed
(PR #430); this module is the backstop for the next such bug, so it degrades to
one failing panel instead of an account ban. It is pure logic with an injected
clock; `guardedFetch.ts` owns the single instance and wires it to the network
and to the other tabs.

## What it does

`RequestLoopGuard.check(key)` returns `ok`, `loop` or `page_budget`, for reads
(GET / HEAD — `isGuardedMethod`) only; writes are never refused.

- **Per URL.** Timestamps per key in a sliding window (`LOOP_WINDOW_MS` = 5s);
  past `LOOP_MAX_PER_WINDOW` (15) the key is blocked. While blocked, an attempt
  within `LOOP_EXTEND_GAP_MS` (2s) of the previous one pushes the release out to
  a full window from now, so a serial loop slowed to ~1 attempt/s by the
  refusal delay never drains the window and leaks nothing, while a normal
  poller caught by a brief burst (3s+ between attempts) is released when the
  window runs out. A URL blocked again within `LOOP_REPEAT_WITHIN_MS` (60s) of
  its release is a repeat offender: its block doubles each time (5s → 60s cap),
  which starves loops that refetch at once on success but back off on failure.
  The release time is when the block actually ended — also for keys pruned
  while idle — so a trip long after the last one is a first offence again.
- **Page budgets, shared across same-origin tabs** (`PAGE_WINDOW_MS` = 60s;
  `onSpend` reports each spend, `recordRemote` counts another tab's, because
  the edge counts per IP). Every admitted read spends the *total* budget
  (`PAGE_TOTAL_MAX` = 500) — enforced only while the backend sits behind the
  cloud edge (`totalBudgetEnabled`, wired in `guardedFetch.ts`); a *rapid*
  repeat (the same URL again within 2s) also spends the *rapid* budget
  (`PAGE_RAPID_MAX` = 150), in every mode.
  - The total budget sits just under the edge ban line (562/min per IP, minus
    room for writes, SSE and static assets). Reads beyond it would get the IP
    banned anyway, so refusing them is never worse for the user than letting
    them through. Ordinary polling scales with the agent count (the 15s
    per-agent chat-history poll is 4 reads/min per agent, plus ~60–100/min of
    other polls), so one visible tab reaches 500 at roughly 85–90 agents — a
    session that would already cross the edge line, so this caps the damage
    rather than causing it. It is the last line against loops no other layer
    sees: a fixed set of 20+ URLs each re-read every 2s+, or a changing query.
  - Against a local backend (desktop app, `run.sh`) there is no edge ban, so
    the total budget is off there; the per-URL and rapid layers still keep a
    loop from hammering the local backend.
  - The rapid budget stops loops over a few URLs, each under the per-URL limit
    (6 URLs × 2/s = 720/min), long before the total does.
  - History: the first version capped all reads at 400/min per tab (exhausted
    by the per-agent poll at ~95 agents and blind across tabs); the next
    counted only rapid repeats and URLs fresh within a minute, which missed the
    fixed-set loop and miscounted tabs returning from the background as fresh.
  A request refused by a budget spends nothing and does not count toward its
  URL's window, but its attempt time is recorded, so a retrying loop keeps
  reading as rapid.

`shouldReport` throttles console reports to once per key per minute. Once more
than 500 keys are tracked, every call prunes keys that are neither blocked,
hit within the window, nor attempted within the last 2s (so pruning cannot hide
a rapid repeat), together with their report and strike bookkeeping, whatever
the verdict — a loop that keeps getting refused is exactly when the maps grow.
`delayUnlessAborted` implements the refusal delay and removes its abort
listener once the timer fires.

## Calibration

Prod access logs (2026-10-29/30, ~17h, all users except the two loop
incidents) show at most 3 identical reads per IP in any 5s window; the fastest
steady poll is 2s. 15 per 5s leaves 5x headroom yet catches the observed loop
(~5 identical reads/s per URL) within about 3s. Largest known legitimate burst
of reads: the bundle export page prefetches 5 reads per selected agent,
so selecting ~100 agents at once would spend the total budget and those
prefetches fail quietly. `setInterval` jitter can make a 2s poller's gap
dip just under 2s and count as rapid; that is at most ~30/min per open viewer,
far under the rapid budget.

## This file doesn't do

- Block writes (POST/PUT/PATCH/DELETE).
- Touch the network or other tabs itself — that wiring is `guardedFetch.ts`.
