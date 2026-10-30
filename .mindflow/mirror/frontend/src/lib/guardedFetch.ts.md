---
code_file: frontend/src/lib/guardedFetch.ts
last_verified: 2026-10-30
stub: false
---

## 2026-10-30 — 全局 fetch 守卫（覆盖插件直接调用的 `fetch`）

插件 bundle 以模块形式跑在本页面的 realm 里，可以直接调全局 `fetch`，源码扫描看不到它们——插件自己的
渲染循环此前没有任何刹车。`installGlobalFetchGuard()`（[[main]] 在 `bootPlugins()` 之前调用，幂等）
保存原生 fetch 后把 `globalThis.fetch` 换成包装：**发往本站 origin 或 API 后端 origin 的请求**走
`guardedFetch`，其它 origin 原样放行（边缘看不到，失败不该占预算）。`rawFetch` 是唯一的不受守卫
通道：安装后指向保存的原生 fetch，未安装（测试）时就是当前全局 fetch；只有本文件与
[[sessionGuard]] 可以用它（源码扫描强制）。此前「不能全局替换，因为 sessionGuard 要用真 fetch」
的顾虑由 `rawFetch` 解决。原生 fetch 存在 `globalThis[Symbol.for('narranexus.nativeFetch')]`
（不是模块变量），dev HMR 重新求值本模块时仍调真 fetch，不会套两层守卫；`urlOf` 对跨 realm 的 URL
对象用 `String(input)` 取 href，避免把不同请求都记成 `undefined` 误触循环封锁。测试用
`__uninstallGlobalFetchGuardForTests` 还原原始（未 bind）的 fetch。首屏体积的余量来自
#434 里 Owner 批准的入口上限 2,385,000。

# guardedFetch.ts — the app's HTTP calls, behind the request-loop guard

## Why it exists

The request-loop guard (`requestLoopGuard.ts`) only protects calls that pass
through it. The first version sat inside `api.request`, but the artifact and
office-watch services, the system log page, blob downloads and other panels
called `fetch` directly — the same effect-driven, polling shapes as the
Channels panel whose loop got a user banned on 2026-10-30. So every HTTP call
now goes through `guardedFetch` (the plugin loader included), and
`__tests__/no-raw-fetch.test.ts` fails the build on any new raw `fetch` — called
or passed as a value — outside its allowlist.

## What it does

`guardedFetch(input, init)` has `fetch`'s signature. Reads (GET / HEAD) are
checked against the page's single `RequestLoopGuard`; a refused read logs one
console error per URL per minute (the endpoint lives only there), waits
`LOOP_BLOCK_DELAY_MS` (rejecting early on `init.signal` abort) and throws
`RequestLoopBlockedError`. Everything else is plain `fetch`, and the `Response`
comes back untouched, so callers keep their own status handling (410 / 409
branches in the artifact code, `ApiError` mapping in `api.request`).

A guarded read whose response status is in `FAILURE_STATUSES` (400 / 403 /
404 / 405) is reported to the guard (`recordFailure`); a success (200–399, 304 included;
an opaque status 0 is not) proves the path (`recordSuccess`). Every admitted read first
takes an in-flight slot from `guard.acquire(url, signal)` (unproven paths
only; it waits when none is free, up to `SLOT_WAIT_MAX_MS`) and releases it in
`finally`, so in one tab a burst of unproven reads reaches the server at most
`EDGE_FAIL_SAFE` at a time. Refusals
by a page budget (and `slot_wait`, whose retry queues again for up to 15s)
throw at once; per-URL refusals and `slot_full` (a full queue would refuse a
retry again at once) wait `LOOP_BLOCK_DELAY_MS` first. A `slot_wait` refusal (no slot freed in time) throws
`RequestLoopBlockedError('slow')`, whose message says the server is slow
(`common.requestSlow`) rather than "too many requests" — the user sent one
action; the backend is what lagged.

The page-wide limits are switched off only when the backend is known to be
local (`knownLocalBackend`: Tauri, forced-local config, runtime mode `local`,
or an API origin on a loopback host — `bash run.sh` in a browser). Forced cloud
/ `cloud-web` and an unknown mode keep them on (fail-closed). In practice the
app resolves the mode to `local` for every build not forced to cloud
(`useResolveAppMode`), so the deciding input is the forced-cloud flag, which
the cloud deployment sets (`NARRANEXUS_FORCE_MODE=cloud`). Because a
deployment with an edge in front but without that flag would silently lose all
page-wide protection, the first time a page decides "local" it logs one
`console.info` line saying the edge limits are off. They were briefly removed with
the volume budget, then restored for this switch only (review I-1 on #434: a
minimal distribution's excluded plugins 404 on every poll, and there is no edge
to protect against locally).

Reports name the limit that was hit and its count for the last minute (budget
reports throttle under one fixed key per budget; per-URL `loop` / `failing`
reports throttle per URL).

The page budgets are shared across same-origin tabs over a `BroadcastChannel`:
each rapid repeat and each failed read is broadcast and counted by the other
tabs (`recordRemote`). The edge counts per IP, so per-tab budgets alone would
let two tabs sum past its failure line. Without BroadcastChannel (or if
construction throws, e.g. an opaque origin) the budgets fall back to per tab.

`__resetRequestLoopGuardForTests()` rebuilds the guard and channel so test
cases cannot leak budget into each other.

## This file doesn't do

- Guard `lib/sessionGuard.ts`'s session probe — a refusal there would read as a
  dead session; it uses `rawFetch`, which the no-raw-fetch scan allows only
  there and in this file (`RAW_FETCH_ALLOWED`).
- Cover a plugin's XMLHttpRequest (e.g. axios), Worker or iframe requests —
  only the main-realm global `fetch` is wrapped. None of the in-tree plugins
  use them; wrapping XHR would be its own change.
- Cover WebSocket / SSE traffic.
- Guard requests to other origins (third-party SDKs, CDNs): the global
  wrapper passes them straight through. The edge never sees them, so their
  failures must not spend the page's budget.
