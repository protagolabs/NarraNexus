---
code_file: frontend/src/pages/DashboardPage.tsx
last_verified: 2026-08-06
stub: false
---

## 2026-08-06 — Chat UI v4:吸收 ManageAgentsPage,监控 + 管理合一

页面重写为 v4 形态:标题 + Agents/Teams segmented 切换 + 4 个 KPITile
(Running / Queued / Errors / Cost today,仅统计 owned agents — public 无
metrics,规避 NaN)+ 搜索/团队过滤条 + 批量操作条(全选 / 选团队 /
加入 / 移出 / 删除,shift-click 范围选择保留)+ **状态×管理合一表**:
checkbox | chevron+头像+name/id | 状态点 | 团队 chips | 来源。行可展开
(**Set 多展开**,替代旧单 expandedId),展开内容复用 AttentionBanners /
QueueBar / MetricsRow / SessionSection / JobsSection / Sparkline /
RecentFeed。数据 = configStore.agents(roster)⨝ dashboardStore.agents
(status)按 agent_id;status 缺席显示 "—" 行。public agents(状态流里
非 roster)渲染只读行,保住旧 PublicCard 的可见性。

不变量全部沿袭:轮询 FSM 节奏归 dashboardStore.computeInterval;清理
必须 active=false + clearTimeout;429 → onRateLimited;tray badge 仅在
计数变化时 setTrayBadge;listenTauri 空返回用 ?.() 解绑。

Teams 视图:团队表(色点/名称/成员数/来源)+ Manage 按钮开既有
TeamManagementModal(内部不动)。AgentCard / DashboardSummary /
DurationDisplay / ConcurrencyBadge / dashboard 版 StatusBadge 删除;
ManageAgentsPage 删除(路由 / TopBar crumb / Settings nav 项一并清理)。
批量删除仍是逐行循环、部分成功弹窗 — 无事务语义,故意的。

> 2026-06-24: Renamed from the mis-named `DashboardPage.md` to the canonical
> `DashboardPage.tsx.md` and rewritten in English to house format. Behavior is
> unchanged — still the polling FSM dashboard mounted at `/app/dashboard`.

# DashboardPage.tsx — Agent Dashboard v2: a self-throttling polling status board

## Why it exists

The cross-agent operations view (`/app/dashboard`): a card grid showing every
agent's run health at a glance, separate from any single agent's chat. It is the
one screen that has to keep itself fresh without a websocket, so its real job is
to poll the backend status endpoint at a rate that adapts to whether the user is
actually looking — cheap when hidden/idle, responsive when focused.

## How it works / design

- **Polling FSM lives in [[dashboardStore]], not here.** The page is a thin view:
  it feeds the store the FSM inputs (`visibility` from the `visibilitychange`
  event, `tauriFocused` from Tauri `tauri://blur` / `tauri://focus`) and runs a
  self-rescheduling `tick()` whose next delay is `store.computeInterval()`. The
  store decides cadence from `visibility × tauri-focus × any_running`; an interval
  of `Infinity` parks the loop entirely (e.g. tab hidden, nothing running).
- **Tray badge is a side effect of polling.** After each successful fetch the
  page computes the running count and calls `setTrayBadge(running)` only when it
  changed (Tauri desktop; web mode is a no-op). This keeps the dock/tray count
  live without a separate loop.
- **429 is handled as backpressure, not an error.** A 429 routes to
  `store.onRateLimited()` (exponential backoff via `computeInterval`) instead of
  the red error banner; other failures go to `onFetchError`.
- **Upstream/downstream**: subscribes to [[dashboardStore]]; renders
  [[DashboardSummary]] (health legend/counts) over a grid of [[AgentCard]] (each
  card owns its own expand/collapse). Data via `api.getDashboardStatus`; tray via
  `lib/tauri`.
- **Gotchas**: the cleanup must set `active=false` AND `clearTimeout` or a stale
  `tick` keeps firing after unmount. `listenTauri` returns null off-desktop, so
  unlisten with `unlistenFn?.()`. One page-level `expandedId` means a single card
  expands at a time (a `Set` would be needed for multi-expand). `DashboardSummary`
  counts are aggregated frontend-side — public agents are forced to
  `healthy_idle` since they carry no `health`; if the backend ever adds health to
  public agents, fix the aggregation too.
