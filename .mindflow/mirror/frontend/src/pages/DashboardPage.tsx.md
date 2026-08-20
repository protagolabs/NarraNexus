---
code_file: frontend/src/pages/DashboardPage.tsx
last_verified: 2026-08-20
stub: false
---

## 2026-08-20 — 顶部 Agents/Teams 切换 → 左侧三标签(和设置一致)

Owner 要求「智能体管理」(=本页,zh title 就叫智能体管理)的视图切换改成
**和 SettingsPage 一样的左侧竖排标签栏**,并拆出第三个「导出」标签:
- `view` 类型扩到 `'agents' | 'teams' | 'export'`。**`?tab=` 是唯一真相源,
  `view` 直接 DERIVE(`parseTab(searchParams.get('tab'))`),没有 useState 副本**
  ——本页是单一 `<Route path="dashboard">`,侧栏 Export 行只是
  `navigate('/app/dashboard?tab=export')` 不会重挂载,派生态天然覆盖「已在
  Dashboard 时深链」。合法 id 只有 `TAB_ITEMS`(模块级常量)一份,不再散成四份。
  `selectTab` 用 `new URLSearchParams(searchParams)` **增量**写回(agents 删 tab,
  teams/export set tab),保留其它参数、URL/侧栏高亮始终一致。
- 外层从居中 `ScrollArea>max-w-960` 改成 `h-full flex flex-col`:标题 header +
  `flex flex-1 min-h-0`(左 `w-56 border-r` nav 三项 + 右 detail)。agents/teams
  两个 pane 仍走 padded ScrollArea+max-w-960;**export pane 内嵌
  `<Suspense fallback={居中 spinner}><BundleExportPage embedded /></Suspense>`**
  ——BundleExportPage 保持 `lazy()` 独立 chunk(与 App.tsx 路由级分包一致),否则
  进 dashboard 就连带下载整个导出向导。fallback 用中性 spinner 不用
  DashboardSkeleton(后者是 dashboard 网格形状,进导出会闪成 layout shift)。它
  自带 h-full 滚动/页脚,故渲染在 padded ScrollArea 之外。`lazy`/`TAB_ITEMS`/
  `parseTab` 等模块级声明放最后一条 import 之后(不夹在 import 里)。
- agents pane 顶部加「创建智能体」按钮(复用 [[../../hooks/useCreateAgent]]);
  teams pane 加「创建团队」按钮(→ `/app/teams/new` CreateTeamPage)。summary
  BracketSectionLabel 从旧 header 移进各 pane。
- 轮询 effect 在 export tab 早退,**依赖是布尔量 `exportOpen = view==='export'`
  而非三值 `view`**:agents↔teams 切换 `exportOpen` 恒 false、依赖不变、轮询循环
  不受打扰(否则每切一次就重启并立刻多打一次 `/dashboard/status`,能自伤成 429);
  只有跨 export 边界才真正停/起。轮询节奏仍归 dashboardStore 所有。
- 待办:三个 pane 仍在同一函数里(~900 行),抽 AgentsPane/TeamsPane 是独立重构,
  记于 `reference/self_notebook/todo/2026-08-20-extract-dashboard-panes.md`。
测试:dashboardTabs.test.tsx、dashboardDeepLink.test.tsx(已挂载深链 + 点 tab 写 URL)。


## 2026-08-19 — review 轮:筛选统一、shift 区间防崩、批量结果如实上报、Manage 带行上下文

- 文本筛选抽成 `matchesFilterText`,public 行同样过它;任何团队筛选下 public 行
  直接不渲染(public agent 没有 roster 团队归属)。空态判断因此自然变成
  「过滤后两段皆空」。
- `filterText`/`filterTeam` 变化即重置 `lastClickedIdx`;shift 区间循环内再加
  一道 `if (!row) continue`——修「筛选变短后 shift-点越界 TypeError 白屏」。
  前者修根因,后者防御索引来源以后再变,两道都要。
- `handleBulkAddToTeam`/`RemoveFromTeam` 改成 `handleBulkDelete` 的形状:
  success/failed 统计 + 失败分支文案(`danger`),remove 路径补结果提示并清空
  选择。新 i18n key `addedToTeamResultWithFailures` / `removedFromTeam*`(×10)。
- Teams 视图行级 Manage 传 `tm.team.team_id`,经 [[TeamManagementModal]] 新的
  `initialTeamId` prop 在 open 上升沿选中——修「每一行都打开第一个团队」的
  误操作入口。
- `TableCheckbox` 加 `onToggle`+`tabIndex`+Space/Enter+`ariaLabel`(点击仍归
  父级 label/cell 保留大热区,焦点/键盘/无障碍名归 checkbox 本体;行级用
  agent 名,表头用全选/反选文案);首个 effect 的两个悬空 promise 显式 `.catch`。
  键盘 toggle 不设 shift 锚点(区间选择是鼠标语义,有意为之)。

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
