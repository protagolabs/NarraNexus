---
code_file: frontend/src/components/dashboard/DashboardSkeleton.tsx
last_verified: 2026-04-13
stub: false
---

# DashboardSkeleton.tsx — Intent

## 为什么存在

v2.2 G1 修复"点击 Dashboard 整页变白屏"。在 `MainLayout.tsx` 的 `<Suspense>` 边界做 fallback——React 在加载 lazy chunk 时显示 skeleton，**Sidebar 保持可见**（不像 App 根 Suspense 会全屏覆盖）。

## 设计决策

- 形状刻意 mimic 真实 dashboard grid（h-7 标题 + h-10 summary bar + 4 个 h-28 卡片）→ chunk 加载完毕后无 layout shift
- 用 `animate-pulse`（Tailwind）而不是自定义 spinner，质感统一
- 没用 framer-motion / 任何动画库——简洁

## 上下游

- 被 `MainLayout.tsx` 的内层 `<Suspense fallback={<DashboardSkeleton />}>` 引用
- 不被任何路由直接渲染，纯 fallback 用途

## Gotcha

- 改 dashboard 真实卡片高度时，建议同步本文件的 `h-28`，避免 swap 时跳行
- `data-testid="dashboard-skeleton"` 用于 vitest 断 "lazy chunk 加载中" 状态
