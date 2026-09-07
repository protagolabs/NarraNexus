---
code_file: frontend/src/platform/loader.ts
last_verified: 2026-09-07
stub: false
---

## 2026-09-03（批 2d）— 插件加载器：元数据先行，代码按激活

`loadPlugins()`：GET `/api/plugin-factory` → 只取 enabled+loaded+有 frontend 的行 → `registerDeclaredUi` 按
manifest `frontend.ui` 给每个 page/panel/command 在注册表里放一个 **gate**（owner=插件 id）→
`registerActivation` → `fireActivation('onStartup')`。`activatePlugin`：桌面走 `plugin://<id>/…`、web 走工场
assets 路由；manifest 有 `integrity` 就先 sha256（`digestImpl` 可注入——jsdom 的 SubtleCrypto 跨 realm 拒收
ArrayBuffer，测试用 node:crypto）比对，再由 Blob URL `import()`（动态 import 没有 integrity 属性，所以自己校验后
再导入），Blob URL 登记到 errorSink 做归因；`plugin.activate(host)`。任何失败只上报不外抛。

## 2026-09-04 · builtin.teams as a feature-level plugin (batch 3c.2)

`loadPlugins` reads `data.builtins` from the factory listing and calls `disableBuiltinUi(id)` for every disabled non-protected builtin, which removes that owner from every shell registry — so a disabled `builtin.teams` has no `/teams/*` pages without any teams-specific code in the loader.

## 2026-09-04 · UI slot points (batch 3d.2)

`registerDeclaredUi` also registers gates for `frontend.ui.conversationKinds` / `messageRenderers` (a renderer gate matching by role / content prefix) / `timelineEvents` (a gate per event type) / `slots` (silent component gates, labelled action gates) and derives `onRenderer:` / `onTimelineEvent:` / `onSlot:` activation events; `disableBuiltinUi` sweeps the new registries too.

## 2026-09-04 · channels as descriptors (batch 4a)

`disableBuiltinUi` also sweeps `ui.channels`.

## 2026-09-07 — command-gate identity, disabledOwners blacklist, illegal-page rejection at registration, assetUrl traversal guard, table-driven SHELL_REGISTRIES (I-1, I-2, C-2, I-5, architecture E3)

The command gate used to detect "has the plugin replaced its own gate yet" by comparing
`real.value.label !== cmd.label` — a plugin that kept the manifest's declared label on its real
command (a very ordinary thing to do) made the gate never hand off, permanently re-running
`fireActivation` on every invocation instead of the real `run()`. Fixed to the same object-identity
check `actionGate.ts` already used: `e.value !== gate`.

`disableBuiltinUi(pluginId)` now also calls `disableOwner(pluginId)` (registry.ts) so a builtin
disabled at boot cannot register again later even via a lazily-loaded contribution point (e.g.
`ui.channels`, `ui.settingsSections`) that runs well after the disable sweep — a one-shot
`removeOwner` at disable time cannot see a registration that has not happened yet.

`registerDeclaredUi` now REJECTS (at registration time, `continue`s past the entry, calls
`reportUiError`) an `/app`-layout page manifest that does not declare `guard: 'protected'`,
instead of letting it reach `PAGES` and only failing later in `pageRoutes.tsx`'s render-time
check. `pageRoutes.tsx`'s own check remains as defense in depth for entries that reach `PAGES`
another way.

`assetUrl` now rejects any manifest asset entry containing a `..` path segment or characters
outside `SAFE_ASSET_ENTRY = /^[A-Za-z0-9._/-]+$/` (I-5's second half — the URL-origin/prefix
hardening is in `host.ts`'s `http.request`).

`SHELL_REGISTRIES` (the array `disableBuiltinUi` iterates to sweep every registry) is now
`Object.values(REGISTRIES)` from `registries/index.ts` instead of a hand-maintained array — four
copies of the 16-registry list used to exist across `host.ts` and `loader.ts`; adding a 17th
registry now only means adding one line to `REGISTRIES` itself.
