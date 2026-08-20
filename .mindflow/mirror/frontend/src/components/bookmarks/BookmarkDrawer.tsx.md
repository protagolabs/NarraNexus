---
code_file: frontend/src/components/bookmarks/BookmarkDrawer.tsx
last_verified: 2026-08-19
stub: false
---

## 2026-08-19(三)— 切换器 props 成判别对;条目可带计数

- `activeTab/onSelectTab/switcherCategories` 三者 all-or-nothing(判别联合):
  之前"默认 STRIP_CATEGORIES + as unknown as"让「传了 team 的 tab 却忘传
  注册表」能编译——下拉列出单聊面板、点了全空白。现在漏传=编译错;
  内部再无断言,调用方(单聊传 STRIP_CATEGORIES、团队传
  teamDrawerCategories(counts))显式给表。
- `DrawerSwitcherTab.count?`:下拉项尾部渲染活计数——不宣传自己的入口
  等于关键时刻是关着的(共享文件此前无处可见数量)。

## 2026-08-19(二)— 切换器泛型化 `<T extends string>`

activeTab/onSelectTab/switcherCategories 以 T 贯通,两个调用方
(AtomicTabId/TeamTabId)恢复端到端类型检查,as 断言删除;默认
STRIP_CATEGORIES 经一次内部断言桥接。

## 2026-08-19 — 切换器注册表可注入(switcherCategories)

标题下拉的面板清单从写死 STRIP_CATEGORIES 变为 prop(默认仍是它);
activeTab/onSelectTab 放宽为 string。团队房间以同一抽屉挂自己的
成员/可视化产物/文件三面板([[../chat/team/teamTabs]]),机制零分叉。

## 2026-08-19 — 标题变面板切换器 + banner 插槽

- 新可选 props `activeTab`/`onSelectTab`:传入时头部标题变成下拉——列出
  [[tabs]] 注册表的全部面板(按 Config/Activity/Narra/Nexus 分组,当前项打勾),
  钉选窗口自己就能换内容,不必回聊天头找按钮。菜单用 [[../../hooks/useDismissOnOutside]]。
  不传则退回纯文本标题(移动端调用方不变)。
- 新可选 `banner`:渲染在头部与内容之间(首跑教学卡 [[DrawerCoachMark]] 用)。
测试:drawerPanelSwitcher.test.tsx。

## 2026-08-06 (2) — 头部 ? 说明气泡

标题右侧新增 HelpCircle 圆圈(description prop 非空才渲染):hover /
focus 浮出 ink 底 paper 字的一句话说明(max-w 280,面板下方展开,
不会被 drawer 的 overflow-hidden 裁掉)。文案由 MainLayout 用
tabDescKey 取,语言随 i18n 切换。

## 2026-08-06 — 桌面临时抽屉改内嵌列 + per-tab 宽度

Owner 两点:①悬浮 overlay 盖住聊天内容(own 头像被挡)→ 桌面端
(inset=true)未 pin 的抽屉也**入流布局**,chat 左移让位;临时语义保留
(backdrop 点击 + Esc 关闭;in-flow 列 z-[201] 压过 z-[200] backdrop,
自身点击不被吃)。真正的 fixed overlay 只剩移动端。②新增 insetWidth:
artifacts 面板 ~50vw(大屏可读性,clamp 保住 sidebar272+chat400 底线),
其余面板 440px;pinned 仍走用户可拖的 pinnedWidth。
「单元素稳定槽位、模式切换不 remount」约束未破坏(drawerPinToggle 测试通过)。

## 2026-07-30 (2) — pin/unpin no longer remounts the panel; the portal is gone

Owner: "点击/取消 然后页面上的交互感觉怪怪的". The weirdness was state loss.
[[MainLayout]] rendered the two modes as two SEPARATE `<BookmarkDrawer>`
elements (one inline in the flex row, one in a `!drawerPinned &&` branch), so
toggling the pin unmounted one and mounted the other. Everything the user had
set up inside the panel reset to defaults — job status filter, view mode,
expanded rows, scroll position. Data survived (it lives in `preloadStore`), the
user's *choices* did not, which is why it read as "the UI changed things behind
my back" rather than as a reload.

**The first fix attempt was wrong, and the tests caught it.** Collapsing to one
element is necessary but not sufficient: this component still returned a bare
`<div>` for pinned and `createPortal(<div>…)` for the slide-over. A portal is
its own node type, so switching in and out of one IS a tree-shape change and
remounts the subtree just the same. React has no reparent primitive — moving a
subtree to a different DOM parent always unmounts it. Worth remembering before
reaching for portals to "just move" live UI.

**What actually works**: the panel's DOM position never moves.
- The slide-over is `position: fixed` (out of flow → consumes no layout space)
  rendered exactly where the pinned column sits. **No portal at all.**
- Both modes are the same `<div>`, differing only in className/style, inside a
  fragment whose child slots don't shift (the backdrop renders as `false` when
  pinned rather than disappearing from the child list).
- So `if (pinned) return …` as a separate early return is now FORBIDDEN here —
  two returns of different shapes reintroduce the remount.

**Accepted cost of dropping the portal**: the overlay now lives in `<main>`'s
stacking context (`relative z-10`) instead of on `<body>`, so it no longer
paints over the fixed sidebar (z-40). The two only overlap on mobile with the
off-canvas nav open, where nav-over-content is the expected behaviour. Modals
(z-1000, still portalled) continue to cover the drawer correctly.

**Also**: the pinned column now owns its own frame and `pinnedWidth` (the
caller used to wrap it in a styled div — a wrapper is exactly the kind of
positional difference that caused the remount), and header buttons finally got
`title` attributes. The pin/unpin/close labels had existed as `aria-label` only
since 2026-06-10, so hovering the pin explained nothing — which is how the
Owner ended up having to ask what the button did.

## 2026-07-30 — the slide-over no longer covers the strip (`edgeReservePx`)

**The bug**: opening a tab (say Awareness) made the rest of the rail
unreachable. Two causes, both in slide-over mode:

1. the panel was anchored `right-0` at 440px, so it sat ON TOP of the 64px
   [[BookmarkStrip]] at the page edge, and
2. the transparent backdrop was `fixed inset-0` with `pointer-events-auto` —
   it covered the strip too, so a click on another tab was swallowed as
   "click outside → close".

Net effect: the user had to X the current panel before any second panel could
be opened. The strip is a *switcher*; making it modal defeats it.

**The fix**: `edgeReservePx` — the width of the right edge the overlay must
leave alone (strip + layout gutter; [[MainLayout]] computes it from
`STRIP_WIDTH_PX`). It is applied to BOTH the panel wrapper and the backdrop,
so the strip stays visible *and* clickable and switching panels is one click.
`aria-modal` is gone for the same reason — the strip beside the drawer is
live, and aria-modal would hide it from screen readers. `role="dialog"` +
`aria-label` stay; Esc and backdrop-click still close.

Default is 0, so a caller with no strip (or mobile, where the strip isn't
rendered) gets the old full-edge overlay.

# BookmarkDrawer.tsx — Slide-over shell for bookmark panels

## 为什么存在

Opening a bookmark must not squeeze the chat — the redesign's promise
is "space goes back to the conversation" (spec §6). So panel content
opens in a right-anchored slide-over that floats OVER the content,
with an explicit pin escape-hatch for power users who want the old
persistent-column behavior back.

## 上下游关系

- **被谁用**: MainLayout's ChatView; children are the Activity /
  Agent-profile panels (M3b).
- **依赖谁**: nothing project-specific — a generic shell (portal,
  backdrop, header with pin/close).

## 设计决策

- **Slide-over by default, pin to become a column** — pinned state is
  controlled by the parent (persisted in localStorage there), because
  only the parent knows how to re-flow the layout around a static
  column.
- Portal + transparent backdrop in slide-over mode; `role="dialog"` +
  `aria-modal` ONLY in slide-over mode. A pinned column is part of the
  page, not a dialog — keeping aria-modal there would trap screen
  readers.
- 440px width (clamped to viewport) — wider than the old 320px column
  on purpose; together with accordions this kills the "endless
  scrolling" complaint.
- Esc / backdrop-click / re-click close only apply when unpinned.

## 新人易踩的坑

`data-drawer-backdrop` is a styling-free hook used by tests; don't
remove it when restyling.
