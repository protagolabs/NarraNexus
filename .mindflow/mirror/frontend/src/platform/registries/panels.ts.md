---
code_file: frontend/src/platform/registries/panels.ts
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — 抽屉面板注册表

`bookmarks/tabs.ts` 继续管条带布局（分类/图标/标签），本注册表只管「tab id → 面板组件」，
`BookmarkPanelHost` 查表渲染而不是 `&&` 链。面板组件统一接 `{ agentId }`。
