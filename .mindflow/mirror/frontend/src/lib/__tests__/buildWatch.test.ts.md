---
code_file: frontend/src/lib/__tests__/buildWatch.test.ts
last_verified: 2026-11-02
stub: false
---

# buildWatch.test.ts — the deployed-build check

Locks [[buildWatch.ts]]: `/build.json` is read with a cache-busting query and
`cache: 'no-store'`; the SPA `index.html` fallback, HTTP / network errors and a
malformed id all read as unknown and never raise the banner; a different id is
reported exactly once and checking stops; the interval and the
tab-visible-again event both re-check; `stop()` removes both; a result landing
after `stop()` is dropped; reads never stack while one is in flight; each read
carries an abort signal.

The fetch is stubbed with minimal response objects whose `json()` settles in
one microtask — a real `Response` parses over several ticks, which a
microtask `flush` does not cover and made the first check look still in flight.
