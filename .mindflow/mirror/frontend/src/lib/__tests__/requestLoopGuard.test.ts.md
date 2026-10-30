---
code_file: frontend/src/lib/__tests__/requestLoopGuard.test.ts
last_verified: 2026-10-30
stub: false
---

# requestLoopGuard.test.ts — the loop guard's contract

Unit tests drive `RequestLoopGuard` with an injected clock:

- per URL: refused past `LOOP_MAX_PER_WINDOW` in `LOOP_WINDOW_MS`; a steady 2s
  poll never is; **a serial loop slowed by the refusal delay leaks zero
  requests over 60s**; a 2s poller caught by a brief burst is released; a loop
  that refetches at once on success but backs off on failure is starved by the
  repeat-offender doubling; a trip long after the previous block ended is a
  first offence again; URLs count independently;
- failures: a URL that keeps failing is blocked for a minute and then
  released; a polling loop on a deleted resource stays far below the edge's
  20-a-minute failure line in its **worst sliding minute** (≤ `PAGE_FAIL_MAX`);
  while the failure budget is spent, a path whose last answer was a success keeps
  working and failing / never-seen paths are refused; a path that worked once
  but now fails is held even when its query changes; the path key carries
  `agent_id` and agrees across relative / absolute spellings; a 404 loop with a changing query is held to
  `PAGE_FAIL_MAX`; **a real-user failure pace (6 a minute) never pauses
  reads**; occasional spread-out failures never block;
- page budgets: a loop over 6 URLs at 2/s each is capped by `PAGE_RAPID_MAX`;
  **ordinary polling is never refused however many agents it covers** (there
  is no volume budget); a busy human page is never refused; another tab's
  messages can at most spend a budget, never pile up beyond it; two guards
  wired through `onSpend` / `recordRemote` share one budget; a request refused
  by a budget does not count toward its URL's window; a block that expires
  untouched and is then pruned keeps its real release time (a trip 70s later
  is a first offence);
- reports are throttled per key; only GET / HEAD are guarded; the delay rejects
  early on abort.

Integration tests: through `api.request` (spied `fetch`) a loop stops reaching
the network and fails with `RequestLoopBlockedError` (still an `ApiError`), the
refusal settles only after the delay, writes are never refused; through
`guardedFetch` the `Response` comes back untouched (a HEAD 410 included) and a
raw-fetch loop is refused with a localized message naming no URL or agent id.
Cross-tab wiring through a real `BroadcastChannel`: another tab's rapid
repeats and failures spend this tab's budgets, and this tab's are announced.
Failure wiring: a read that keeps answering 404 stops reaching the network,
a read that answered with a success keeps working while other reads exhaust the budget,
once a read has failed 25 concurrent first reads reach the network at most
`EDGE_FAIL_SAFE` times (< 20), one stray failure still lets an ordinary panel
switch's 15 first reads all go out, a clean page's burst of 40 successful first reads all goes out,
reads settling by network error release their pending slot,
its failures are announced to other tabs, and failing writes are never
counted. Each case starts
from `__resetRequestLoopGuardForTests()`.

In-flight slots and runtime mode (guardedFetch): a crafted burst of 250
failing first reads on a clean page puts at most `EDGE_FAIL_SAFE` on the wire
and never more than that in flight; a clean burst of 40 first reads all goes
out, at most `EDGE_FAIL_SAFE` at a time; a page-budget refusal is immediate; a
read waiting for a slot rejects on abort and leaks no slot; a read that finds
no slot within `SLOT_WAIT_MAX_MS` behind a hung backend is refused (and never
sent); a newcomer never overtakes a queued read, even when capacity frees by failure
expiry rather than a release; a queued read whose URL got fail-blocked meanwhile
is refused unsent; a queued read is re-judged when served (a path proven
meanwhile, or edge limits switched off, needs no slot); a status-0 answer does
not prove a path; dispose refuses queued reads; a served queued read leaves no
timer behind and is reported once; a queued read is let through when
the oldest failure leaves the window even though no slot is ever released; a local backend
(mode `local`, or no mode on a loopback origin) has no page-wide limits; an
unknown mode with a non-loopback API keeps them (fail-closed). The file runs
in `cloud-web` mode by default, because jsdom's localhost origin would read as
a local backend.

Every mechanism was checked red on revert: wiring, block extension, the 2s gap,
escalation, release time, prune backfill, the failure block and failure and
rapid budgets, failure reporting in guardedFetch, in-flight counting and its
the slot limit, immediate budget refusals, the local-backend switch and its
fail-closed default, the agent_id path key,
cross-tab sharing (class and both channel directions), and not counting
budget refusals per URL.
