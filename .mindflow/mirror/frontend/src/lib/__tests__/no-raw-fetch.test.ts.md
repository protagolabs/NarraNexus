---
code_file: frontend/src/lib/__tests__/no-raw-fetch.test.ts
last_verified: 2026-10-30
stub: false
---

# no-raw-fetch.test.ts — every network call goes through guardedFetch

A source scan over `frontend/src` (skipping tests, mocks and comment lines)
that fails on any `fetch` called (`fetch(`), passed as a value (`?? fetch;`,
`= fetch,`, `fetch.bind(`) or reached through the global object
(`window.fetch`, `window['fetch']`) outside
a short allowlist with stated reasons (`lib/guardedFetch.ts` itself and
`lib/sessionGuard.ts`'s session probe). A raw fetch bypasses the request-loop
guard, and per-component tests cannot catch that because each stubs the fetch
it tests; only a scan covers files not written yet. Its first run found a call
the manual sweep had missed (`productAnalytics.ts`, written `void fetch(`); the
value forms were added after review found `platform/loader.ts` passing `fetch`
as a default (`deps.fetchImpl ?? fetch`).
