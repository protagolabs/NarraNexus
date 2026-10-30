---
code_file: frontend/src/lib/apiError.ts
last_verified: 2026-10-30
stub: false
---

## 2026-10-30 — `RequestLoopBlockedError(reason)`

可选参数 `'limit' | 'slow'`（默认 `'limit'`）：`'slow'` 用于守卫的 `slot_wait` 拒绝（在途槽位
15 秒内没有释放——服务端慢），文案取 `common.requestSlow`（10 个 locale 都已加），不再和限流共用
「请求过多」，免得把慢后端说成用户请求太多。

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
