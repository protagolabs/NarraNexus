---
code_file: frontend/src/pages/DashboardPage.tsx
last_verified: 2026-04-13
stub: false
---

# DashboardPage.tsx

## 为什么存在
Dashboard v2 主页面组件。挂在 `/app/dashboard` 路由（App.tsx）。

## 协作
- 订阅 `dashboardStore` 的 agents / error / FSM 输入
- `visibilitychange` → `setVisibility`
- Tauri `tauri://blur` / `tauri://focus` 事件 → `setTauriFocused`（通过 `lib/tauri.ts::listenTauri`）
- 轮询循环：`tick()` 拿数据 → `setTrayBadge(runningCount)` 仅当变化 → 下次 `setTimeout(tick, computeInterval())`

## 渲染
- 顶部 `<DashboardSummary>` — 彩色健康计数条，双充当图例（v2.1.1+）
- 卡片网格：`<AgentCard>` 自己管内部展开/折叠（v2.1.1+）。外置 `AgentCardExpanded` 已删
- error 态 + 空态文案

## 单 `expandedId` 策略
页面级一个 `expandedId: string | null`，同一时刻只能展开一张卡。点击同一张两次 = 折叠；点击另一张 = 切换。想支持多张同时展开需要改成 `Set<agentId>`。

## Gotcha
- 清理函数必须把 `active=false` + clearTimeout，否则卸载后 tick 继续发请求
- Tauri event listener 不存在时 `listenTauri` 返 null，调用方需 `unlistenFn?.()`
- DO NOT 把 `action_line` 传给 `dangerouslySetInnerHTML`（eslint `no-restricted-syntax` 会挡）
- **`DashboardSummary` 的 agent 数是前端聚合的**——后端返回的 agents 列表里包含自有 + public，统计 health 时 public 一律算 `healthy_idle`（没有 `health` 字段）。如果未来改后端让 public 也带 health，前端统计逻辑也要改
