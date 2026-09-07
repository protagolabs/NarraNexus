---
code_file: frontend/src/platform/bootPlugins.ts
last_verified: 2026-09-07
stub: false
---

## 2026-09-03（批 2d）— 应用级装配

`main.tsx` 在内置注册之后调用：先把 errorSink 的上报器指向 `POST /api/plugin-factory/{id}/errors`，再
`loadPlugins()`；不阻塞首帧。

## 2026-09-07 — re-run `loadPlugins()` on the absent→present auth edge (M-9)

`bootPlugins()` runs once, before React mounts — often while the user is not yet authenticated,
so `GET /api/plugin-factory` 401s and the first `loadPlugins()` call returns `[]` with no retry.
A user who logs in during the SAME SPA session (no full reload) previously never got their
plugins until a refresh. Fixed by subscribing to `useConfigStore` and re-running `loadPlugins()`
the instant `isLoggedIn` flips `false → true` (a subsequent login-while-already-logged-in, e.g.
switching accounts without an intervening logout, is NOT this edge and does not re-trigger it —
only the transition matters). Safe because `registerDeclaredUi`'s id-collision check treats the
SAME owner re-declaring an already-registered id as a no-op, not a reported conflict (see
`loader.ts`'s mirror doc, M-2/M-9 interaction).

`bootPlugins()`'s return type changed from `Promise<void>` to `Promise<() => void>` (the store
subscription's unsubscribe) purely for testability — `main.tsx`'s `void bootPlugins()` call site
is unaffected (it already discards the return value); tests use the returned unsubscribe to keep
each test's subscriber from leaking onto the next test via the module-level `useConfigStore`
singleton.
