---
code_file: frontend/src/components/layout/Sidebar.tsx
last_verified: 2026-08-25
stub: false
---

## 2026-08-25 (3) — Messenger 合并回 team 房间

Owner 要求把 team 也放回 Messenger 快捷入口里——2026-08-25 那版只列了
agent,把 `AgentList` 原本置顶的 TEAMS 分组丢了。改法不是恢复一个独立
TEAMS 分组,而是让 [[MessengerSection.tsx]] 把 agent 会话和 team 群聊房间
按同一条"最近活跃"时间轴合并排序(`messengerUtils.ts` 的
`sortMessengerItems`),team 行用 `GroupAvatar`(carbon·silicon 双色环,跟
已删的 `TeamChatRow` 同款视觉)+ `teamHasUnread` 状态点(没有 agent 那种
streaming 信号,团队房间没有单一"是否在跑"的廉价判断)。点击 team 行跳
`/app/teams/:id/chat`,读标记仍由 `TeamChatPanel` 自己在打开时写
(`markTeamRead`),本组件不重复那份记账。顶部文件头注释("teams + agents,
owned by AgentList")本身早已过期(AgentList 已删),顺手改成指向
MessengerSection。

## 2026-08-25 (2) — Messenger 挪进 Zone 2b,不再挤在 Configure 边框内

Owner 反馈:Messenger 展开后列表被 `max-h-[280px]` 卡死,下面还留一块纯空白
(Zone 2b 那个 `<div className="flex-1" />` 占位符)一直空到 footer,而
每行 agent 又太小。两处都是同一个根因——Messenger 被塞进了 Configure 那个
**不参与 flex-1 的**边框内 `<div>` 里,跟 Zone 2b 的占位 spacer 是分开的
两块。修法:把 `<MessengerSection />` 从 Configure 的 `<div>` 里搬出来,
直接替换掉 Zone 2b 那个 spacer div(见下方 2026-08-24 条目);
`MessengerSection.tsx` 自己变成 `flex-1 min-h-0`,展开时内部列表也换成
`flex-1 min-h-0 overflow-y-auto`(不再是定高 max-height)。收起时表现跟
原 spacer 完全一样(footer 照常被顶到底部);展开时列表会长满这块区域,
不会再有多余空白。行内头像从 `xs`(24px)换成 `sm`(32px),padding
`py-1.5`→`py-2.5`,预览文字 11px→12px。DOM 顺序上仍然是"System 下面就是
Messenger"——只是不再共享同一个边框容器。

## 2026-08-25 — Messenger:System 下方新增可展开的最近对话快捷入口

2026-08-24 把整个聊天列表撤走后,回到某个具体 agent 的对话要走「导航项 →
管理表 → 聊天图标」三跳。Owner 反馈这条路径太绕,补了一个折中方案——**不是
恢复 AgentList**,是在 Configure 分组的 System 行下方新增一个同款样式
(icon + label + hover/active 背景)的导航行 [[MessengerSection.tsx]]:
默认收起,点击展开成一个按最近活跃排序的只读列表(头像 + 状态点 + agent
name 作为标题 + 时间 + 最后一条消息预览),再点一次收起。这一行**没有
"+" 新建聊天按钮**(Owner 明确要求只保留展开/收起这一个交互)。列表本身
的排序/预览提取逻辑放在 [[messengerUtils.ts]] 里,是从已删除的
`AgentList.tsx`/`agentGroupUtils.ts` 里抽出核心算法重写的纯函数,不是复活
那两个文件——改名/清空数据/删除等管理操作依然只在 Dashboard 表格行里。

## 2026-08-24 — 导航区分组重排；聊天列表整个撤走

Zone 2a 从一列平铺按钮（New / Export / Dashboard / Marketplace / Settings /
System）改成三段：顶部无标题区（Search — 新增，打开既有 `⌘K`
CommandPalette / New / Export / Marketplace）→ **Workspace** 分组（Agents /
Squads，都路由到 `/app/dashboard`，靠 `?view=agents|teams` 决定打开哪张
表，跟 [[../../pages/DashboardPage.tsx]] 自己的 segmented toggle 共用同一个
query param 双向同步）→ **Configure** 分组（Settings / System，System 仍走
`features.showSystemPage` 门控）。旧的单独 "Dashboard" 按钮撤下 —— Agents /
Squads 这两个入口本身就是 dashboard 的两张表,不再需要一个中立的第三按钮。

Zone 2b 原来整块渲染 `<AgentList />`（每个 agent/team 都是侧栏里一行,点哪
个就直接进那个聊天）,现在**整个撤走**,换成一个 `flex-1` 占位 div 把
footer 顶到底部。找回 / 打开某个具体 agent 或 team 的聊天,现在要先进
Agents 或 Squads 表,再从表格行里点「进入聊天」图标 —— IA 从
「侧栏常驻列表」变成「导航项 → 管理表 → 具体聊天」三层。`AgentList.tsx` /
`AgentGroupSection.tsx` / `TeamChatRow.tsx` / `agentGroupUtils.ts` 连同各自
测试一并删除;被删文件里"批量操作"相关能力（改名/编辑简介/清空数据/删除/
公开开关,team 侧的加成员/改名/清空/删除)没有跟着丢——迁到了
[[../../pages/DashboardPage.tsx]] 的表格行里,复用同一批
[[AgentRowMenu.tsx]] / [[TeamRowMenu.tsx]] / [[ClearAgentDataDialog.tsx]] /
[[EditAgentDialog.tsx]] / `ClearTeamDataDialog.tsx` 组件,只是挂载点从
AgentList 换成了 DashboardPage。

`sidebar.agent-list` / `sidebar.team-section` 两个 `data-help-id` 锚点随
AgentList/AgentGroupSection 一起消失,[[../help/helpContent.ts]] 里对应的
帮助气泡改指向新的 `sidebar.squads` 锚点。

## 2026-08-20 — Create agent 入口改跳表单（弹窗）

New 菜单的 Create agent 从 `() => void createAgent()`(一键建空白
Agent)改为 `() => navigate('/app/agents/new')`(新
[[../../pages/CreateAgentPage.tsx]])。`useCreateAgent()` 的引入和
`creatingAgent` disabled 态一并从本文件移除 —— 页面内部的 busy 态在
`CreateAgentPage` 自己管。`AgentList.tsx` / `OnboardingChecklist.tsx`
里各自的快速建空白 Agent 按钮不受影响。

## 2026-08-06 (3) — 账户弹层三项指向 /app/account

Account / Billing / Subscription 从 navigate('/app/settings?tab=account')
改为 navigate('/app/account')(Settings 的 account 面板已迁出,见
[[../../pages/AccountPage.tsx]])。仍以 netmindToken 存在为显示条件。

## 2026-08-06 (2) — Create team 入口改跳页

New 菜单的 Create team 从打开 TeamManagementModal 改为 navigate
('/app/teams/new')(新 [[../../pages/CreateTeamPage.tsx]]);modal 挂载
从 Sidebar 移除(Dashboard Teams 视图仍在用它管理既有团队)。

## 2026-08-06 — Chat UI v4:三层结构重写

按 v4 设计稿(specs/2026-08-06-chat-ui-v4-design.dc.html)整体重排:
① logo + 收起按钮(panel 图标);② 全局导航列 — New(下拉:Create agent /
Create team / Import .nxbundle / Import from other source,见 [[CreateMenu.tsx]])
/ Export / Dashboard / Marketplace / Settings / System(gated),之后才是
Chats 列表;③ 底部用户行 → **账户弹层**(工作台 / Account / Billing /
Subscription(netmindToken 才显示)/ Theme / Language(内联展开列表)/
Logout)+ Find Us 外链(从 TopBar 迁入,保持 target=_blank + noopener)。

关键不变量(沿袭):logout / 切模式仍是 wipeAllSessionData() + **硬跳转**
window.location.href;SHOW_MODE_SWITCHER 逻辑保留在弹层里未删。
收起态改为 **整个 aside 隐藏**(uiStore.sidebarCollapsed,md:hidden),
72px 图标栏退役;展开按钮在聊天头部 / MainLayout 浮动 chip 上,不在本组件。
TeamManagementModal / ImportAgentModal 的挂载点从 AgentList 移到这里。
宽度 288→272px。ThemeToggle / LanguageToggle 组件不再被本文件引用
(账户弹层内联实现),组件本身保留给其他调用方。
last_verified: 2026-08-12
stub: false
---

## 2026-08-12 — 悬停预取 DashboardPage 补 `.catch`（复审二轮 🟢）

`prefetchDashboard` 的 `import('@/pages/DashboardPage')` 加 `.catch(() => {})`——删掉全局 `vite:preloadError` 监听后没人 `preventDefault`,后台预取失败本应静默(真导航会重试、[[ChunkErrorBoundary.tsx]] 兜到达 render 的),把「故意忽略」写进代码而非留成 unhandled rejection。纯加固,无行为变化。

## 2026-07-28 — Beta 徽章挂到 logo 旁

展开态 logo 旁新增 [[BetaBadge.tsx]](wrapper `gap-0`→`gap-2`),向用户声明
产品处于 Beta 阶段;悬停出多语言预期管理说明。收起态 logo 本就隐藏,徽章
随之不渲染。

## 2026-07-21 — Marketplace 一级入口

Dashboard 与 Settings 之间新增「Marketplace」导航(Store icon,展开/收起
两种形态都有),路由 `/app/marketplace` → pages/MarketplacePage。


## 2026-07-10 (4) — footer "clear history" button removed

The footer 🗑️ "clear history" button (active-agent scope) is gone, along with
`handleClearHistory` and the now-unused `api` / `Trash2` / `agentId` refs.
Clearing moved to the per-agent ⋮ menu as a scoped multi-select wipe
([[AgentRowMenu.tsx]] → [[ClearAgentDataDialog.tsx]]); the old button was also
ineffective (DB-only, never cleared the on-disk narratives/trajectories).

## 2026-07-10 (3) — footer 反馈入口改为移动端专属

桌面端入口已是右下角浮动 [[FeedbackButton.tsx]]；footer 这个入口用
`isMobile` 门控保留,因为移动端右下角归 composer。每个视口恰好一个入口。


## 2026-07-10 (2) — 反馈入口移出 footer

Owner 反馈 footer 位置不好；入口移到右下角浮动按钮
[[FeedbackButton.tsx]]（问号正上方），Sidebar 恢复到无反馈入口状态。


## 2026-07-10 — footer 反馈入口

footer 行（ThemeToggle/LanguageToggle 旁）新增 MessageSquarePlus 图标按钮，
打开 [[FeedbackDialog.tsx]]。入口刻意放常驻 footer——显式反馈是 Agent 自动
submit_feedback 的兜底通道，必须随时可达。


## 2026-07-03 — cloud/local mode switcher hidden

Both sidebar entry points to the mode switcher (expanded button+popup and the
collapsed icon button) are gated behind a module-local `SHOW_MODE_SWITCHER =
false` — users should not choose the deployment mode. All switching logic
(handleSwitchMode, mode state, /mode-select navigation, the data-wipe on
switch) is kept intact and still referenced (so lint stays clean); only the UI
is hidden. Flip the flag to true to restore the control.

> 2026-06-24: Sidebar is now the shell for the TEAMS/AGENTS restructure. It no
> longer owns any team grouping itself — it just renders `<AgentList collapsed/>`
> inside the ScrollArea, and AgentList is what splits the roster into a TEAMS
> section (each row a team group-chat) above a flat AGENTS section. The old
> `TeamFilterBar` chip-filter approach is gone for good.

## 2026-06-24 — user header avatar → sm + "YOU ›" affordance

The expanded user-header `RingAvatar` is `size="sm"` (32px) to match the agent
rows + team avatars (uniform avatar size across the sidebar). A right-aligned
**"YOU ›"** cue (mono label + `ChevronRight`) marks the row as clickable → the
"You" workspace; faint at rest, carbon on hover/active. It's a visual cue inside
the row-button, not a nested button.

## 2026-06-23 — user avatar opens the "You" workspace

The carbon user-info block (avatar + name + Online) is a `<button>` navigating to
`/app/you` ([[YouWorkspace]]) — the owner-scoped Memory / Network / World + Notes
page, the carbon counterpart to selecting a silicon agent. Both expanded and
collapsed variants carry the click + active highlight when on `/app/you`.

## 2026-06-11 — show NetMind nickname, not the opaque userSystemCode

user_id is a 32-hex NetMind userSystemCode in cloud mode. The user block shows
`displayName || userId` (configStore.displayName = NetMind nickName), falling
back to user_id in local mode where it IS the chosen username.

# Sidebar.tsx — Collapsible left rail: branding, user, grouped nav, mode-switch

## Why it exists

Single place that owns the persistent left-rail shell: branding, the carbon
user-header (→ "You" workspace), the grouped nav (Search / New / Export /
Marketplace, then Workspace: Agents / Squads, then Configure: Settings /
System), the local↔cloud mode-switcher, and the destructive logout action.
Collapsible to hidden (the whole aside, not an icon rail); on mobile it becomes
an off-canvas drawer toggled from the TopBar.

## How it works / design

- **It is a shell with no list to *manage* anymore (2026-08-24), plus one
  read-only quick-switcher (2026-08-25).** The sidebar used to delegate the
  whole chat roster to `AgentList` (a TEAMS section of group-chat rows over
  a flat AGENTS section, with per-row rename/clear/delete) — that component
  and its grouping helpers are deleted. Agents/Squads are now plain nav rows
  that route to [[../../pages/DashboardPage.tsx]] (`?view=agents` /
  `?view=teams`); all *management* still happens from a table row there.
  What came back is narrower: [[MessengerSection.tsx]], a collapsible
  "Messenger" row (below System) that expands into a read-only,
  most-recent-first list for jumping straight into a recent conversation —
  no rename/clear/delete/public-toggle, no "+" new-chat action, just expand
  and pick. Sidebar's own concerns otherwise stay chrome only: logo, user
  header, nav, footer.
- **Hard reload on mode-switch & logout.** Both call `wipeAllSessionData()`
  (`logout()` + `clearChat()` + `clearPreload()` to reset Zustand, then direct
  `localStorage.removeItem()` of every known persisted key) and then
  `window.location.href = '/…'` — a full document reload, NOT React Router
  `navigate()`. A soft navigate keeps the React tree, closure-captured store
  snapshots and module caches from the prior mode alive, which is exactly how
  cloud data bled into a subsequent local session. The direct removeItem calls
  are the authoritative clear; we don't trust persist to flush before reload.
- **Upstream/downstream**: rendered by [[MainLayout]]; depends on
  [[../../pages/DashboardPage.tsx]] (Agents/Squads destination),
  `useConfigStore` / `useChatStore` / `useRuntimeStore` / `usePreloadStore` /
  `useUIStore`, and `react-router-dom`'s `useSearchParams` (reads the same
  `?view=` the page's own toggle writes, so the two stay in sync). Dashboard
  nav prefetches the lazy `DashboardPage` chunk on hover/focus (static literal
  → Vite-resolved, no injection risk) to pair with MainLayout's inner Suspense.
- **Gotchas**: the System link is feature-flagged behind
  `features.showSystemPage` (runtimeStore). The mode-switch popup is a raw
  positioned `div`, not a Popover — it doesn't close on outside-click; you toggle
  it by clicking the button again.
