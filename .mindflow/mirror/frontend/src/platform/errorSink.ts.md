---
code_file: frontend/src/platform/errorSink.ts
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — UI 崩溃的归因与订阅

之前渲染崩溃只到 `console.error`。现在 `ChunkErrorBoundary` 同时上报到 sink。归因是**尽力而为的
启发式**：`error.stack` 文本里含某个已登记的 chunk URL 前缀就归到那个插件 id（`attributeChunkUrl`
由批 2 的插件加载器登记），否则归 `shell`；浏览器栈格式不统一，所以可能漏归（不会错归到别的插件，
除非两个前缀互为前缀）。批 1 还没有订阅者——工场页健康视图与后端上报在批 2 接入；最近 50 条留在
内存供其读取。监听器自己抛错不会盖掉原错误；`onUiError` 的退订函数返回 void。
