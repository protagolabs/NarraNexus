---
code_file: frontend/src/lib/__tests__/requestLoopGuard.test.ts
last_verified: 2026-10-30
stub: false
---

# requestLoopGuard.test.ts — the loop guard's contract

Unit tests drive `RequestLoopGuard` with an injected clock:

- an identical GET is refused past `LOOP_MAX_PER_WINDOW` in `LOOP_WINDOW_MS`;
  a steady 2s poll never is;
- **a serial loop slowed by `LOOP_BLOCK_DELAY_MS` leaks zero requests over 60s**
  — the regression a plain sliding window had (it drains and lets a fresh burst
  through every few seconds); this case fails if the block-extension branch is
  removed;
- a 2s poller caught by a brief burst is released rather than blocked until
  reload (fails if every blocked attempt extends the block);
- a loop that refetches at once on success but backs off 1s on failure leaks
  at most ~2 windows' worth per minute once escalated (fails without the
  repeat-offender doubling), while a trip long after the previous block ended
  is a first offence again (5s, not escalated);
- a URL is released only after a full quiet window; URLs count independently;
- the page-wide budget caps a changing-query loop at `PAGE_GET_MAX_PER_WINDOW`
  per minute and recovers afterwards, while a 281/min human pace (measured per-IP
  peak) is never refused;
- reports are throttled per key; only GET is guarded; the block delay rejects
  early on abort.

Integration tests go through `api.request` (spied `fetch`): a GET loop stops
reaching the network and fails with `RequestLoopBlockedError` (still an
`ApiError`), the refusal settles only after the block delay, and writes to the
same URL are never refused. Each integration case uses a random agent id so the
module-level guard instance cannot leak state between cases.
