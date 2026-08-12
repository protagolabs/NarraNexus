---
code_file: frontend/src/components/ChunkErrorBoundary.tsx
last_verified: 2026-08-12
stub: false
---

# ChunkErrorBoundary.tsx — 崩溃兜底 + stale-chunk 自愈,区分两类

## 为什么存在

Mark item 10。路由级 boundary,包住整个 `<Suspense><Routes>`（[[App.tsx]]）。到达 render 的崩溃有两类,**必须区分**（复审 item 5）:
- **stale-chunk 404**(发版артефакт):`componentDidCatch` 里 `isChunkLoadError` 命中 → `reloadOncePerSession` 一次自愈([[chunkReload.ts]]);UI 显示「有新版本 / 刷新」。
- **真 render bug**:不自愈,UI 显示中性的「出错了 / 刷新重试」——**不能**一律说「有新版本」,否则用户刷新后照崩、陷死循环还不报障,bug 被永久掩盖。

## 设计要点

- class 组件(error boundary 必须)。`getDerivedStateFromError` 存 error;`componentDidCatch` 打日志 + 决定是否自愈。
- **上报**:目前只有带 tag 的 `console.warn`(chunk)/`console.error`(真 bug)。前端暂无 error sink,且鉴权版 analytics 端点承载不了登出态(/login)崩溃 → 专用 render-crash beacon 记为 follow-up。**本次的承重修复是「真 bug 不再伪装成新版本」**,不是遥测。
- **render 刻意少依赖**:inline 样式 + 纯英文,CSS/i18n 正是失败源时仍能渲染兜底。

见 `components/__tests__/ChunkErrorBoundary.test.tsx`（chunk→新版本 / 真 bug→中性 / 正常→children）。
