---
code_file: frontend/src/lib/chunkReload.ts
last_verified: 2026-08-12
stub: false
---

# chunkReload.ts — 部署期 stale-chunk 404 一次性自愈

## 为什么存在

Mark item 10。路由用 `React.lazy` 切 chunk（[[App.tsx]]）。发版后老 tab 仍引用**旧 hash**的 chunk 文件名，下一次导航的 dynamic import 404，Vite 在 window 抛 `vite:preloadError`。一次 reload 拉到新 `index.html` + chunk manifest 即恢复。

## 设计要点

- `handlePreloadError(reload, storage)`：`reload`/`storage` 注入以便测试（不碰真 window/sessionStorage）。**会话级 guard**（`nx-chunk-reloaded`）保证每 session 最多刷一次——真正坏掉的构建（chunk 永久 404）不会把 tab 卡进无限刷新循环；同 session 第二次发版（罕见）由 [[ChunkErrorBoundary.tsx]] 的手动「刷新」兜底。
- `installChunkReload()`：在 [[main.tsx]] 启动时调一次，`preventDefault` 掉默认 uncaught rejection 后走 `handlePreloadError`。

见 `lib/__tests__/chunkReload.test.ts`（一次刷 / 不重复刷）。
