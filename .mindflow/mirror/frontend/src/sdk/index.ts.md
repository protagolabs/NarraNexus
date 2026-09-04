---
code_file: frontend/src/sdk/index.ts
last_verified: 2026-09-04
stub: false
---

## 2026-09-03（批 2e）— `@narranexus/sdk`（暂在 app 工程内）

插件前端只从这里拿东西：`definePlugin`、`HostAPI` 类型、各注册表条目类型、`vitePreset`/`HOST_EXTERNALS`/
`hostShimModule`、主题 token 表。放在 `frontend/src/sdk/` 是为了与宿主一起类型检查与测试；批 6 发成独立 npm 包，
表面不变。

## 2026-09-04 · UI slot points (batch 3d.2)

Re-exports the slot-point / renderer / timeline / when types for plugin authors.

## 2026-09-04 · channels as descriptors (batch 4a)

Exports `ChannelDef` / `ChannelStatus` / `ChannelConfigProps`.

## 2026-09-04 · generic channel credentials (batch 4b)

Exports `GenericChannelConfig` / `makeGenericChannelConfig` for channel plugins.
