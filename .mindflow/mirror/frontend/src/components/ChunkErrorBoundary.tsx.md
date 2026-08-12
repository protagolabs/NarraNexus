---
code_file: frontend/src/components/ChunkErrorBoundary.tsx
last_verified: 2026-08-12
stub: false
---

# ChunkErrorBoundary.tsx — 崩溃兜底成「刷新」提示而非白屏

## 为什么存在

Mark item 10。最常见触发是发版后 stale lazy-chunk 404：dynamic import 抛「Failed to fetch dynamically imported module」，Suspense 把它抛给最近的 error boundary——没有 boundary 就整树卸载成**白屏**（本次要修的 bug）。[[chunkReload.ts]] 处理 `vite:preloadError` 事件路径的一次性自动刷新；本 boundary 是**到达 render 的兜底**（同 session 第二次发版、或任何其他 render 崩溃），显示可见的「刷新」按钮。

## 设计要点

- class 组件（`getDerivedStateFromError` + `componentDidCatch`）——error boundary 必须是 class。
- **刻意少依赖**：inline 样式 + 纯英文文案，即使 app CSS / i18n 资源正是失败的一部分也能渲染出兜底 UI。
- 包在 [[App.tsx]] 的 `<Suspense><Routes>` 外层。

见 `components/__tests__/ChunkErrorBoundary.test.tsx`（子组件抛错→出现 refresh 按钮；正常→原样渲染）。
