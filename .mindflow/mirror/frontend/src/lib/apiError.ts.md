---
code_file: frontend/src/lib/apiError.ts
last_verified: 2026-10-30
stub: false
---

# apiError.ts — error types shared by the API client and guardedFetch

## Why it exists

`ApiError` used to live in `api.ts`. `guardedFetch.ts` (which `api.ts` now
builds on) must throw `RequestLoopBlockedError`, a subclass, so both moved here
to avoid an import cycle. `api.ts` re-exports them; callers keep importing from
`@/lib/api`.

## Design notes

- `ApiError` carries the HTTP `status` and the backend's raw `detail`, so
  callers branch on status instead of string-matching messages.
- `RequestLoopBlockedError` has `status` 0 — every existing consumer compares
  against specific codes (404 / 422 / 429), so a local refusal is never
  mistaken for a server answer, and `sessionGuard` never sees it (it fires only
  on real 401 responses). Its message is the localized `common.requestBlocked`
  and names no URL: dozens of panels render `e.message` directly, and the old
  English message leaked internal paths and agent ids into the UI. The endpoint
  is reported to the console by `guardedFetch` instead.
