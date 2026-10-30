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

`RequestLoopGuard.check(key)` returns `ok`, `loop`, `failing`, `rapid_budget`
or `fail_budget` (so reports can say which limit was hit), for reads
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
- **Failures.** `guardedFetch.ts` reports every guarded read answered with a
  `FAILURE_STATUSES` code (400/403/404/405 — exactly what the edge monitor
  counts as probing) through `recordFailure`. `FAIL_MAX` (3) failures of one
  URL within `FAIL_WINDOW_MS` (10s) block it for `FAIL_BLOCK_MS` (60s), so a
  loop on a deleted agent's 404 leaks at most 3 failures per URL per ~70s.
  Across URLs (a loop whose query changes every call), the page may fail at
  most `PAGE_FAIL_MAX` (10) reads per minute. Past that, a read is refused
  unless the last answer for its **path** was a 2xx (`recordSuccess`). The path
  key is the URL path plus its `agent_id` query parameter if any (several
  endpoints take the agent there: `/api/jobs?agent_id=…`), with origin, other
  parameters and fragment dropped — so a 403 on someone else's agent does not
  mark the viewer's own agent's jobs as failing, relative and absolute
  spellings agree, and a loop that changes its query is still one path; paths whose last answer was a failure, and paths never
  seen, are refused until the oldest failure leaves the window. So a burst of
  403s from one panel (e.g. a non-owner clicking through someone else's public
  agent) cannot freeze the agent list or the chat, while a loop that changes
  its query every call is still one failing path and stays held. Never-seen
  paths are refused on purpose (fail-closed): a loop over freshly built paths
  is otherwise invisible. The last-outcome map is per tab and capped at 1000
  paths, oldest evicted first. 401 / 429 / 5xx are not counted:
  they are session/throttle/server conditions, not probing, and the edge
  ignores them too.
- **In-flight reads count once anything has failed.** Failures are known only
  when the response arrives, so a panel switch to an agent the viewer cannot
  read (a dozen first reads at once) could overshoot. Once at least one read
  has failed in the window, every admitted read of a path not known to work is
  registered by `track()` until it settles (2xx, failure, network error or
  abort — `guardedFetch` releases it in `finally`), and unproven reads are
  refused while landed failures plus those in flight reach `EDGE_FAIL_SAFE`
  (18, under the edge's 20). This is a separate, higher line than
  `PAGE_FAIL_MAX` (10 landed failures) on purpose: one stray 403 followed by
  opening an unvisited agent of the viewer's own (~10–15 first reads from
  `preloadAll` and the panels) must all go out. In-flight counts are per tab;
  two tabs bursting at the same moment can still sum past the line.
- **Clean-page gap.** With no failure in the window nothing is counted, so the
  first load's burst of parallel reads is never held back. The cost: the very
  first navigation in a fresh tab to an agent the viewer cannot read (e.g. a
  crafted link to someone else's public agent) sends its whole first-read set
  uncapped — bounded by the size of one agent's panel set (~10–15 reads),
  under the edge line. A stale or foreign selection replayed on
  every load is also removed at the root (`configStore.refreshAgents` clears a
  selection the server does not list; defaults never pick a stranger's agent).
- **Known limit.** A path whose answers alternate between 2xx and failure
  stays "proven"; only the per-URL failure block holds it.
- **Local mode too.** The failure layers also run against a local backend
  (desktop app, `run.sh`), where no edge bans. That is intended: they hold a
  failing loop off the local backend as well, and with paths that answered 2xx
  exempt from the page pause, a normal session cannot be frozen by it.
- **Rapid budget, shared across same-origin tabs** (`PAGE_WINDOW_MS` = 60s;
  `onSpend` reports each spend, `recordRemote` counts another tab's, because
  the edge counts per IP). A *rapid* repeat (the same URL again within 2s)
  spends it (`PAGE_RAPID_MAX` = 150); it stops loops over a few URLs, each
  under the per-URL limit (6 URLs × 2/s = 720/min). The failure budget is
  shared the same way (during a deploy, an old tab still sends `'total'` and a
  new one `'fail'`; each ignores the other's message until reloaded, so the
  budgets are per tab for that overlap). `recordRemote` is capped at the budget, so a burst of
  messages from other tabs can at most spend it, never lock a tab out for
  longer than a window.
- **No volume budget.** Request volume alone is not judged (Owner decision,
  2026-10-30, monitoring v2): the edge no longer bans on it, and ordinary
  polling must never be refused however many agents it covers. History: a
  per-tab 400/min cap (exhausted by the per-agent poll at ~95 agents), then a
  500/min page-wide total budget sitting under the old edge flood line; both
  are gone with that line.
  A request refused by a budget spends nothing and does not count toward its
  URL's window, but its attempt time is recorded, so a retrying loop keeps
  reading as rapid.

`shouldReport` throttles console reports to once per key per minute. Once more
than 500 keys are tracked, every call prunes keys that are neither blocked
(loop or failing), hit or failed within their windows, nor attempted within
the last 2s (so pruning cannot hide a rapid repeat or a failing URL), together
with their report and strike bookkeeping, whatever the verdict — a loop that
keeps getting refused is exactly when the maps grow. `delayUnlessAborted`
implements the refusal delay and removes its abort listener once the timer
fires.

## Calibration

Prod access logs (2026-10-29/30, ~17h, all users except the two loop
incidents) show at most 3 identical reads per IP in any 5s window; the fastest
steady poll is 2s. 15 per 5s leaves 5x headroom yet catches the observed loop
(~5 identical reads/s per URL) within about 3s. Real users' 400/403/404/405
failures peak at 6 per IP per minute; scanners and the 10-28 attacker run
25–500. The edge bans at 20 failures a minute, so `PAGE_FAIL_MAX` = 10 keeps a
buggy client at half the ban line while a real user's worst minute fits.
`setInterval` jitter can make a 2s poller's gap dip just under 2s and count as
rapid; that is at most ~30/min per open viewer, far under the rapid budget.

## This file doesn't do

- Block writes (POST/PUT/PATCH/DELETE).
- Touch the network or other tabs itself — that wiring is `guardedFetch.ts`.
