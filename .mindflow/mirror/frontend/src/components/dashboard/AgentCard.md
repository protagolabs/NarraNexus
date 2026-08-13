---
code_file: frontend/src/components/dashboard/AgentCard.tsx
last_verified: 2026-04-13
stub: false
---

## v2.2 改动（2026-04-13）

- **G2 effectiveHealth**：rail / cardTint 不再 dim 原色。引入 `acknowledgedHealthOf(health, allDismissed, kind)`（healthColors.ts），全 dismiss 时：
  - error → `acknowledged`（中性 slate rail + 右上角 `data-testid="ack-dot"` 红点 ring）。**Security-M1: error 永不降级到绿色**——用户 dismiss 是 "ack" 不是 "fixed"
  - warning / paused → healthy_running 或 healthy_idle（视 kind）
  - 其他 → 不变
- **G3 stale badge**：`agent.stale_instances` 非空时 header 加 `data-testid="stale-badge"` 灰色 pill（"N stale" + module_class tooltip）；不触发 banner、不算 error
- **G4 视觉 polish**：`rounded-2xl` + shadow-sm/hover:shadow-lg + backdrop-blur；name `text-[15px] font-semibold tracking-tight`；StatusBadge 包成 pill；expand/collapse 用 `grid-rows-[0fr → 1fr]` 200ms 过渡（无 framer-motion）；`▾ more` hover 偏移 1px + 变色
- **新 data-attr**：`data-server-health=<原始 health>`（debug 用，区别于 `data-health=<effective>`）
- 旧 `railDimClass`/`opacity-40` 全部移除（v2.1.2 的妥协方案被 G2 effectiveHealth 取代）


# AgentCard.tsx — Intent

## 为什么存在
Dashboard 网格里每个 Agent 对应一张卡片，这是**唯一**的卡片实现。渐进式披露的**执行者**：folded 时给"身份 + verb + banner + 内联关键数字"，点击展开后有完整 session/job/sparkline/recent feed。

今天一天内此文件被重写 4 次（v2 → v2.1 → v2.1.1 → v2.1.2），每次响应用户直接反馈，详见 git log。

## 两个子组件（权限分叉）
- **`<OwnedCard>`** 渲染 v2.1 rich 字段（verb_line, attention_banners, queue, sessions, jobs, sparkline, recent_events, metrics_today）
- **`<PublicCard>`** 只渲染 header（name + kind + concurrency bucket + duration）

这是**组件级权限边界**——配合 Pydantic `PublicAgentStatus extra='forbid'` 形成防泄漏双保险。即使后端意外多传字段，前端也不 access，自然不显示。`AgentCard` 本身只根据 `owned_by_viewer` 分派。

## 数据契约
消费 `OwnedAgentStatus` 全部字段。关键点：
- `health` → 左侧 rail 颜色（映射在 `healthColors.ts`）
- `verb_line` → 主叙事（humanized，后端 `humanize_verb` 生成）
- `attention_banners` → 可 dismiss 的顶部通知（`<AttentionBanners>`）
- `queue` + `metrics_today` → 内联紧凑条（`<QueueBar compact>` + `<MetricsRow>`）
- `sessions` / `running_jobs` / `pending_jobs` → 展开后的 section
- `recent_events` → 展开后的折叠 feed
- Sparkline 懒加载（自己 fetch `/agents/{id}/sparkline`）

## 交互
- **卡片身 onClick = toggle expand**（`onToggleExpand` prop，上游 `DashboardPage` 维护 `expandedId`）
- 所有内部交互元素（banner `[×]`、section header、item rows、action buttons）都 `e.stopPropagation()` — 不冒泡触发卡片展开
- `role="button"` + `tabIndex={0}` + `onKeyDown` (Enter/Space) → 键盘可达
- `▾ more / ▴ less` 只是视觉提示，不再是按钮——整张卡就是按钮

## v2.1.2 新行为：rail dim
`useAllBannersDismissed(keys)` 读 sessionStorage 判定是否所有 banner 都被 dismiss。是 → rail + card tint 加 `opacity-40`。语义："用户已经 acknowledge 所有告警 → 视觉降级到安静状态"。新 banner 出现（signature 变）自动 un-dim。

## 依赖关系
```
AgentCard
├── StatusBadge, DurationDisplay, ConcurrencyBadge   (header)
├── AttentionBanners                                  (dismissible)
├── SessionSection → SessionItem (lazy session detail)
├── JobsSection → JobItem (lazy job detail + retry/pause/resume)
├── QueueBar (compact mode + full mode)
├── Sparkline (独立 fetch)
├── RecentFeed (collapsible)
├── MetricsRow
└── healthColors (palette) + expandState (useExpanded + useAllBannersDismissed)
```

## Gotcha
- **`AgentCardExpanded.tsx` 已被移除**（v2.1.1 retire）。v2 时用作外置展开容器，v2.1 起卡片自管展开，外置容器冗余。如果在别处看到引用——历史遗迹，删除即可。
- **`running_count` 不是 ConcurrencyBadge 的显示依据（owned）**。v2.1.1 起 owned 完全不渲染 ConcurrencyBadge——`verb_line` 给出类型 + 数量，比孤立 `×N` 清楚。
- **`stopPropagation` 是强契约**：新加内部交互元素必须显式 stopPropagation，否则意外触发卡片展开。eslint 不保护，靠人盯。
- **单 `expandedId` 同时只展开一张**（`DashboardPage` 状态）。想支持多张同时展开需改页面级状态。
- **`idle_long` opacity-75 + banner-dismissed opacity-40 可叠加**——两者都 true 时视觉非常淡，符合"不打扰"意图。
