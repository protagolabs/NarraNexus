---
code_file: frontend/src/components/bookmarks/builtinTabIds.ts
last_verified: 2026-09-07
stub: false
---

# builtinTabIds.ts — 壳自有 rail tab id 词表

## 2026-09-07（批 1 三轮复审移植）— 新建

条带从 `PANELS` 注册表派生后，`AtomicTabId` 放开成 `string`，壳自己手写的 id（`platform/builtin.ts` 的注册、
ChatHeader 的固定分组）失去了编译期校验——拼错一个 id 是运行时空面板。本文件是那份唯一的字面清单 + 类型；
`builtin.test.ts` 钉住「注册表里 owner=builtin.ui 且带 strip 的 tab」恰好等于这份清单，两边不会漂。
