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
- page budgets: a changing-query loop and a loop over 20 fixed URLs each read
  every 2s (both invisible to the per-URL layer) are capped at
  `PAGE_TOTAL_MAX`; a loop over 6 URLs at 2/s each is capped by
  `PAGE_RAPID_MAX`; **100 agents polled every 15s plus the other background
  polls are never refused**; a 281/min human pace is never refused; the total
  budget can be switched off (local backend); another tab's messages can at
  most spend a budget, never pile up beyond it; two guards
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
Cross-tab wiring through a real `BroadcastChannel`: another tab's spends
make this tab refuse in cloud mode (and the report states the count at the edge
line), an unknown mode with a non-loopback API keeps the total budget on
(fail-closed), a local mode or a loopback backend ignores it, and this tab's
reads are announced. Each case starts
from `__resetRequestLoopGuardForTests()`.

Every mechanism was checked red on revert: wiring, block extension, the 2s gap,
escalation, release time, prune backfill, the total and rapid budgets,
cross-tab sharing (class and both channel directions), and not counting
budget refusals per URL.
