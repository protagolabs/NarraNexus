---
code_file: frontend/src/components/layout/MainLayout.tsx
last_verified: 2026-08-19
stub: false
---

## 2026-08-19 — 钉选默认开 + 宽度上限跟视口 + 首跑教学

- 抽屉默认**钉选**(只有显式 unpin 存 '0' 才关)——面板应该待在原地,
  除非用户说不。策略/键值抽到 [[drawerLayout]](纯函数可测)。
- 拖拽上限从写死 720px 改为 `min(60vw, vw−672)`:大屏上 artifacts 能拉过
  半屏;672 = 侧栏 272 + 聊天列最小 400,永远吃不进去。存量宽度按当前视口
  重新 clamp。
- 首跑(桌面、无 opened-once 标记):懒初始化直接以 artifacts 面板 + 
  [[../bookmarks/DrawerCoachMark]] 教学卡开局(不用 effect,无级联渲染;
  once 标记在 `claimFirstRunAutoOpen` 内一次性占用,手机访问不烧掉桌面首跑)。
  unpin/close/知道了 任一操作即消失。抽屉列仅在有 agent 时渲染,所以
  setup 期间不闪面板。
- 抽屉接上 `activeTab`/`onSelectTab`(标题下拉切换,见 [[../bookmarks/BookmarkDrawer]])。
测试:drawerLayout.test.ts。

## 2026-08-06 (5) — 抽屉 inset + artifacts 50vw

BookmarkDrawer 传 inset={!isMobile} 与 per-tab insetWidth
(artifacts → min(max(440px, 50vw), calc(100vw - 672px)),其他 440)。

## 2026-08-06 (4) — 折叠展开钮从浮动 chip 改为保留左轨

浮动 absolute chip 会盖住子页面标题(Marketplace/Dashboard 截图实证)。
改为 flex 内的 44px 左轨(border-r + nm-paper 底,顶端放展开钮),
子页面/团队聊天内容整体右移,零遮挡。聊天视图仍由 ChatHeader 内联展开钮
负责,不出轨。

## 2026-08-06 (3) — 侧边 Artifact 栏退役

Owner 指定 artifacts 一律从聊天头部入口访问:ChatView 里的 ArtifactColumn
侧栏、chat↔artifact 分栏拖拽全套(chatSplit / contentFrozen 冻结 / 
chat_artifact_split_v1 持久化)以及移动端 Chat/Artifacts tab 切换全部删除。
移动端保留一条工具行(artifacts 按钮 → requestPanel('artifacts') + cost
chip)。loadPinned 保留在 ChatView(头部徽标数与 drawer 面板都靠它水合)。
artifactStore.collapsed 从此无人写(组件内部未动,留待后续清理)。

## 2026-08-06 (2) — Chat UI v4:满铺 + BookmarkStrip 退役

- ChatView / TeamChatView 去掉 p-2/p-3 与圆角描边卡片 — 聊天面到边,
  分隔靠 hairline 与拖拽柄。RAIL_GUTTER_PX 常量随 strip 一起删除。
- 右缘 BookmarkStrip 删除;所有面板入口在 ChatHeader(icons + ⋯ 菜单),
  统一走 uiStore.requestPanel → pendingPanel effect(现在带 toggle 语义:
  重复请求当前打开的 tab = 关闭,对齐旧 strip 行为)。BookmarkDrawer
  单实例约束不变,edgeReservePx=0。

## 2026-08-06 — Chat UI v4:TopBar 降级为移动端专属

TopBar 仅 < md 渲染(自带 md:hidden);桌面端侧栏占满全高。CommandPalette
与全局 ⌘K 监听从 TopBar 迁入本组件(uiStore.paletteOpen),移动/桌面共用
一个实例。新增:sidebarCollapsed(uiStore)时,子页面/团队聊天渲染左上角
浮动展开 chip(聊天视图的展开按钮在 ChatPanel 头部内联,不在这里)。
BookmarkDrawer 单实例约束、resize 两段拖拽、RAIL_GUTTER_PX 同步规则不变。

## 2026-08-13 — shared stacking slot for the two privacy disclosures

[[WebAnalyticsNotice.tsx]] (one-time third-party GTM disclosure) and
[[TelemetryNotice.tsx]] now share ONE bottom-anchored `flex-col gap-3` slot
here, instead of each `fixed`-positioning itself. The slot is
`pointer-events-none` (cards are `pointer-events-auto`) so it never blocks the
composer when both are hidden; the components render plain `w-full` cards. This
replaced a hardcoded `bottom-28` offset on WebAnalyticsNotice that overlapped
telemetry's banner once its body wrapped. WebAnalyticsNotice is listed first =
renders on top. Both self-gate, so mounting is unconditional.

## 2026-08-12 — initReplyLanguageSync 挂 MainLayout(r2 修正)

round-1 误挂 ChatView(team-first / settings 深链用户永远不 mount)。现挂 MainLayout 本体,与 TelemetryNotice/FeedbackButton 同层同理由。

## 2026-08-11 — 挂载 TelemetryNotice(一次性遥测告知)

挂在 **MainLayout 层、FeedbackButton 旁**(预审修正:初版挂进了
ChatView——团队页用户和 settings 深链用户永远不渲染 ChatView,
"只有聊天用户收到的告知"不是告知;FeedbackButton 当年被提升到
MainLayout 就是同一个原因,注释里写着)。自门控(看过 / 遥测未
激活时渲染 null),不分移动端(告知义务不按屏宽豁免)。

## 2026-08-04 — 根容器 h-screen → h-dvh-safe（自带 vh 兜底）

移动端 100vh 把浏览器可伸缩 UI 背后的空间也算进去，布局底边（输入框
附近）被压在工具栏下面露不全。用 index.css 的 `.h-dvh-safe`（100vh
兜底 + 100dvh 覆盖）而不是 Tailwind 的 h-dvh：Tauri 声明 macOS 12.0
最低版本，而 WebKit 12.3 才支持 dvh——纯 dvh 在 12.0-12.2 会整条丢弃、
根容器塌成 auto 高度。App.tsx（PageFallback）和 SetupPage 的整屏根
同批换成该类。artifact 滑不动修复批次的次要项（Base recvpm05jsLg3o）。

> 2026-07-30: mounts [[MigrationGuide]] beside [[OnboardingChecklist]] above the
> chat panel — the local-only, once-per-user "import your other agents" guided
> flow (welcome modal → import, or a coach-mark pointing at the sidebar "+").
> Superseded the earlier MigrationNudge banner.

## 2026-07-30 (2) — one drawer element, not two

The pinned column and the slide-over used to be two separate
`<BookmarkDrawer>` elements in different tree positions, which remounted the
panel (and dropped the user's in-panel state) on every pin toggle — see
[[BookmarkDrawer]]'s entry for the full diagnosis and why the obvious
"one element, keep the portal" fix does NOT work.

Now: a single `<BookmarkDrawer>` at the pinned column's slot, always rendered
when there's an agent, with `open`/`pinned`/`pinnedWidth`/`columnRef` as props.
It positions itself `fixed` when unpinned, so occupying that slot in the flex
row costs nothing in slide-over mode. The styled wrapper div that used to frame
the pinned column moved INTO the component — a wrapper on one branch only is
itself a positional difference, i.e. another remount.

**Do not re-split this on the grounds that "the two modes look unrelated".**
They are one element on purpose, and `drawerPinToggle.test.tsx` fails
immediately if they aren't.

## 2026-07-30 — right-side UX pass: strip never covered, everything drags

Owner report, three symptoms, one theme — the right side behaved as if each
panel owned the whole edge:

**1. The strip got buried.** Opening a tab covered the [[BookmarkStrip]] and
its backdrop ate the strip's clicks, so a second panel required closing the
first. Fixed in [[BookmarkDrawer]] via `edgeReservePx`; this file computes it
as `STRIP_WIDTH_PX + RAIL_GUTTER_PX` (0 on mobile, where no strip renders).
`RAIL_GUTTER_PX = 12` is `<main>`'s `md:p-3` — the strip is desktop-only, so
`p-2` can never be the value in play. **If that padding changes, this constant
must change with it**; nothing links them automatically.

**2. The pinned drawer was a hardcoded `w-[400px]`.** Now `drawerWidth` state
(300–720px, persisted under `bookmark_drawer_width_v1`) with its own
[[ResizableDivider]] to its left. Width grows leftward from the drawer's right
edge, which the strip pins in place — so the edge is a stable reference during
the drag and the chat+artifact group absorbs the change.

**3. Dragging "felt broken"** because iteration 2 (below) deliberately did not
move the panes — only a thin ghost line tracked the cursor and the panes
jumped on release. Correct for perf, unreadable as an interaction.

### Resize, iteration 3: live-follow with neither cost

The ghost line is gone; the panes track the cursor. Both costs that iterations
1 and 2 traded against each other are now paid off separately:

- **React renders** — `handleResize` writes `flexGrow` directly to
  `chatColRef` / `artifactColRef` (and `width` to `drawerColRef`). Zero
  renders during the drag; one on release, which re-asserts through React the
  same values already written imperatively, so there is no visible snap.
- **iframe reflow** — the reason iteration 1 was rejected. Handled at the
  other end: `onResizeStart` sets `dragging`, which passes `contentFrozen` to
  [[ArtifactColumn]]; that column pins its content width for the drag, so the
  sandboxed artifact `<iframe>` is not reflowed 60×/s. Release unfreezes →
  exactly one reflow, at the chosen width.

**Ordering gotcha**: `dragging` must be set from `onResizeStart` (pointerdown),
NOT from the first `onResize`. Setting it on first move re-renders *after* the
imperative `flexGrow` write and clobbers it — the pane visibly snaps back on
the first pixel of movement.

**`pendingSplitRef` still matters**: `onResizeEnd` receives the release
`clientX`, but a pointer released outside the clamp range must commit the last
clamped value, not the raw one.

## 2026-07-28 — TeamChatPanel import follows the package move

`TeamChatPanel` now lives in `@/components/chat/team` (the group-chat surface
grew a console and a guide, so it became a package — 铁律 #23). Import path
only; `TeamChatView` is unchanged.


## 2026-07-10 (2) — FeedbackButton 提到顶层

原先误挂在 `ChatView()` 内（只在聊天页+桌面端可见,子页面丢入口）。现挂在
MainLayout 顶层 return,`aboveHelp={!isSubPage && !teamChatId}` 让它在有 "?"
时上移、无 "?" 时占角位。移动端不挂（入口在 Sidebar drawer footer）。


## 2026-07-10 — FeedbackButton 挂载

与 HelpButton 同点位挂载 [[FeedbackButton.tsx]]（desktop only，同一
!isMobile 条件——移动端右下角保留给内容的理由同样适用于反馈按钮）。


## 2026-06-23 — TeamChatView in the main slot

Added `TeamChatView` (wraps [[TeamChatPanel]]). The route `/app/teams/:id/chat`
is matched off `location.pathname` and rendered in the SAME main slot as
`ChatView` — NOT as a sub-page Outlet with the close-X — so switching between a
single agent and a team's group chat feels seamless. `isSubPage` excludes the
team-chat path.

## 2026-06-11 — atomic-tab drawer

Owner IA revision: drawerTab is now an AtomicTabId (one tab = one
panel); focusKey deep-linking removed (the atomic tab IS the
destination). Drawer content renders via [[BookmarkPanelHost]] (lazy
per panel — the click-latency fix); title from the [[tabs]] registry.

## 2026-06-10 — Context column retired; bookmark strip + drawer

The permanent right context column (ContextPanelHeader/Content, 5 tabs)
is gone. ChatView now renders, right of the chat+artifact group:
optional pinned-drawer column → [[BookmarkStrip]] (~36px, always) →
slide-over [[BookmarkDrawer]] (portal, default). Drawer state lives
here: `drawerTab` / `drawerFocusKey` / `drawerPinned` (persisted under
`bookmark_drawer_pinned_v1`); first open writes
`bookmark_drawer_opened_v1` for the onboarding step. Chat+artifact
group went flex-[5] → flex-1; [[useBookmarkSignals]] is mounted here;
CostPopover moved to the chat card's top-right corner. Re-clicking the
open big bookmark toggles the drawer closed.

## 2026-05-21 — onboarding checklist above the chat

The chat-column card became a `flex flex-col` hosting `<OnboardingChecklist/>`
on top + `<ChatPanel/>` in a `flex-1 min-h-0` wrapper. The checklist is
cloud-only and self-hiding (renders null when not applicable / dismissed),
so when it's absent the layout is byte-identical to before — ChatPanel
just fills the column. The `min-h-0` wrapper is required so ChatPanel's
`h-full` still resolves once a sibling is above it.

## 2026-05-14 — User-resizable chat ↔ artifacts split

- Chat column and `ArtifactColumn` now live inside a shared `flex-[5]`
  inner group; the legacy `flex-[3]` / `flex-[2]` on each was the
  default 3:2 ratio in disguise, so the joint share stays at 5 and the
  Context column's `flex-[2]` is untouched.
- New `chatSplit` state (fraction of joint area occupied by chat),
  default 0.6 — equivalent to the legacy ratio. Persisted in
  `localStorage` under the key `chat_artifact_split_v1` so refresh
  preserves the user's choice.
- `[[ResizableDivider]]` is rendered between the two panes.
- The divider is **only rendered when the artifact column is in
  expanded mode** (`agentId && artifacts.length > 0 && !collapsed`).
  In sliver mode the artifact pane is a fixed 36-px button and
  resizing it would be pointless / misleading.
- `ArtifactColumn` accepts the optional `flexGrow` prop and switches to
  `style={{ flexGrow, flexBasis: 0 }}` when set. The legacy `flex-[2]`
  is kept as the fallback in case someone renders the column directly
  without `MainLayout`.

### Resize perf — ghost-line drag, commit on release (2026-05-14, 2 iterations)

**Iteration 1** moved the columns imperatively during the drag (wrote
`flexGrow` straight to the DOM, no React render). That killed the
React-render cost, but the columns *still physically resized* every
frame — and resizing the artifact pane reflows whatever it hosts. An
HTML artifact is a sandboxed `<iframe>`; reflowing it 60×/s, especially
while **shrinking**, was still visibly janky.

**Iteration 2 (current)** stops moving the columns during the drag
entirely. Only a thin "ghost" preview line tracks the cursor:
- `computeSplit(clientX)` — pure helper, maps pointer X against the
  group's `getBoundingClientRect()` to a clamped fraction (honours the
  `MIN_CHAT_PX` / `MIN_ARTIFACT_PX` per-pane minimums).
- `handleResize` — the divider's `onResize` (rAF-coalesced). Sets the
  `ghostLineRef` element's `left` + `display:block`. The real columns
  are **not touched** → zero reflow during the drag. Stashes the value
  in `pendingSplitRef`.
- `handleResizeEnd` — the divider's `onResizeEnd`. Hides the ghost line
  and does one `setChatSplit` → one re-render → the columns resize and
  their content reflows **exactly once**, and the persist `useEffect`
  fires.
- The ghost line is an `absolute`-positioned `<div>` inside the (now
  `relative`) chat+artifact group; it only renders alongside the
  divider (expanded mode). `ArtifactColumn` is back to a plain function
  component — the `forwardRef` from iteration 1 is gone, nothing needs
  a DOM handle to it anymore.

## v2.3-r3 改动（2026-05-08-r3）

- **WS lifecycle removed**: `connectWs(agentId)` and `disconnectWs()` calls removed from the `useEffect`. The dedicated `/ws/artifacts/{agentId}` endpoint was dropped; artifact signals arrive via the chat WS stream (`tool_output` frames in `ChatPanel.tsx`).
- `loadPinned(agentId)` is still called on mount/agent-change to hydrate agent-scoped artifacts.
- The `connectWs` and `disconnectWs` selectors are no longer imported from `useArtifactStore`.

## v2.3 改动（2026-05-08）

- **4-column layout**: `ChatView` now renders `<ArtifactColumn agentId={agentId} />` between the chat column and the context column. `ArtifactColumn` auto-hides when no artifacts are loaded, so the layout degrades gracefully to 3 columns for agents that don't produce artifacts.
- **Session-ID gap**: `chatStore` does not expose a per-agent session ID (`AgentChatState` has no `sessionId` field). `loadForSession` is intentionally not called. If a session-ID source is added to `chatStore` in the future, add a `loadForSession(agentId, sessionId)` call here.
- **ASCII diagram updated**: file header now shows 4 columns.

## v2.2 改动（2026-04-13）

- **G1 内层 Suspense**：`<Outlet />` 包了 `<Suspense fallback={<DashboardSkeleton />}>`。理由：App.tsx 外层 Suspense 一旦触发会全屏覆盖（包括 Sidebar），用户感受是"点击后整页消失"。内层 Suspense 把 fallback 限在主内容区，Sidebar 始终可见 → 慢导航问题修复。
- DashboardSkeleton 的形状刻意 mimic 真实 dashboard grid，避免 swap 时跳行。

# MainLayout.tsx — Four-column app shell and React Router layout

## 为什么存在

React Router renders this as the layout wrapper for all `/app/*` routes. It decides whether to show the default `ChatView` (chat + artifact column + right panel) or delegate to `<Outlet />` for sub-pages like Settings and System.

## 上下游关系
- **被谁用**: React Router route config (`/app` layout route).
- **依赖谁**: `Sidebar`, `ChatPanel`, `ArtifactColumn`, `ContextPanelHeader`, `ContextPanelContent`, `AgentCompletionToast`, `usePreloadStore`, `useConfigStore`, `useArtifactStore`, `useAutoRefresh`.

## 设计决策

`isSubPage` is detected by checking `location.pathname` — if not `/app/chat` or `/app`, the `<Outlet />` gets the full remaining width with no right panel. This avoids needing a nested router layout for each sub-page.

`ChatView` is a separate named export (not inline) so it can be referenced without instantiating `MainLayout`. It owns the `ContextTab` state — the right-panel tab selection does not persist across navigation.

`preloadAll` is called whenever `agentId` or `userId` changes. This is the entry-point for populating `usePreloadStore` with awareness, social network, inbox, and other agent-specific data.

The artifact WS lifecycle (`connectWs` / `disconnectWs`) lives in `ChatView`'s `useEffect(_, [agentId])`. This ensures the artifact stream tracks the currently selected agent and is torn down on agent switch or unmount.

## Gotcha / 边界情况

`onAgentComplete` is passed to `ChatPanel` as a callback that calls `refreshAll` from `useAutoRefresh`. This is the mechanism that refreshes the right-panel data after an agent run finishes.

**Right-panel height chain (must keep `flex flex-col` on the framed wrapper)**: the `<div>` that wraps `<ContextPanelContent>` carries both `overflow-hidden` (visual clipping for the bordered frame) and `flex flex-col` (so the chain flex-1 → ContextPanelContent's flex-1 → Card's h-full → CardContent's overflow-y-auto can resolve a real height). Removing `flex flex-col` breaks the chain — every right-side panel renders at content height, gets clipped, and the user sees a "tab won't scroll" bug. There is no React state hint in this file telling future editors that the className is load-bearing; this paragraph is the warning.

**ArtifactColumn conditional rendering**: `{agentId && <ArtifactColumn agentId={agentId} />}` — when `agentId` is falsy (no agent selected), the column is omitted entirely. Inside `ArtifactColumn`, if `artifacts.length === 0`, it also returns `null`. The net result: the column only occupies layout space when there is both an active agent AND at least one artifact loaded.
