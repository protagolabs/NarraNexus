---
code_file: frontend/src/platform/errorSink.ts
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — UI 崩溃的归因与订阅

之前渲染崩溃只到 `console.error`。现在 `ChunkErrorBoundary` 同时上报到 sink：按错误栈里出现的
chunk URL 前缀把崩溃归到某个插件 id（`attributeChunkUrl` 由批 2 的插件加载器登记），否则归 `shell`；
订阅者（工场页健康视图、后端上报）拿到 `UiErrorReport`；最近 50 条留在内存供工场页。
监听器自己抛错不会盖掉原错误。
