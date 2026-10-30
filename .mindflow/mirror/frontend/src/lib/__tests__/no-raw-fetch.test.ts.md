---
code_file: frontend/src/lib/__tests__/no-raw-fetch.test.ts
last_verified: 2026-10-30
stub: false
---

# no-raw-fetch.test.ts — every network call goes through guardedFetch

A source scan over `frontend/src` (skipping tests and mocks; matched over the
whole file so calls split across lines are seen, then matches on comment lines
are dropped) that fails on any `fetch` called (`fetch(`), passed as a value (`?? fetch;`,
`= fetch,`, `fetch.bind(`) or reached through the global object
(`window.fetch`, `window['fetch']`), or any other egress API (`new
XMLHttpRequest`, `new EventSource`, `sendBeacon`) outside
a short allowlist with stated reasons (`lib/guardedFetch.ts` itself and
`lib/sessionGuard.ts`'s session probe). A raw fetch bypasses the request-loop
guard, and per-component tests cannot catch that because each stubs the fetch
it tests; only a scan covers files not written yet. Its first run found a call
the manual sweep had missed (`productAnalytics.ts`, written `void fetch(`); the
value forms were added after review found `platform/loader.ts` passing `fetch`
as a default (`deps.fetchImpl ?? fetch`).

A second case runs the matcher against a fixed list of shapes (split-line
value use, `bind`, global-object access, the other egress APIs, and
non-matches such as `guardedFetch`, `refetch`, `query.fetch` and prose) so the
scan's own coverage cannot silently shrink.
