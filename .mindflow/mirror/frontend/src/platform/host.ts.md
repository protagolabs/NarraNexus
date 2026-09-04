---
code_file: frontend/src/platform/host.ts
last_verified: 2026-09-04
stub: false
---

## 2026-09-03（批 2d）— `HostAPI`：插件 `activate(host)` 拿到的全部宿主面

刻意最小（Grafana Angular 教训）：共享框架库（react/react-dom/router/zustand/i18next/lucide，插件 bundle 把它们
标 external，用宿主单份）、六个注册表、`register()`（以插件 id 注册、撤销挂到 `subscriptions`；只允许替换
自己 owner 的条目——即加载器替它登记的 gate）、`http.request`（只收相对路径、带会话鉴权头）、
`http.prefix=/api/x/<id>`、`i18n`（自动 `plugin:<id>` 命名空间）、`log.error`（归因到本插件）、`dispose()`
逆序展开。`exposeHostGlobals()` 把框架库挂到 `window.__narranexus_host__` 一次，供 SDK vite preset 的
externals 解析。stores/组件/内部实现一律不承诺。

## 2026-09-04 · UI slot points (batch 3d.2)

`HostAPI.registries` gains `messageRenderers`, `timelineEvents`, `conversationKinds` and the six slot points (`chatHeaderActions`, `composerExtensions`, `messageActions`, `sidebarSections`, `agentCardBadges`, `topBarItems`); `host.register` works on them like on the structural registries (disposer tracked, may replace only its own gate).
