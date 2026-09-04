---
code_file: frontend/src/components/bookmarks/tabs.ts
last_verified: 2026-09-04
stub: false
---

## 2026-09-04 — `visibleCategories` 删除

dev（#383）退役了抽屉切换器，分组形态再无生产消费者；只留扁平的 `visibleTabs`，消费方是
ChatHeader ⋯ 菜单与 ⌘K。

## 2026-09-04 — `conditional: 'studio'` 也在「可恢复」时提供

`TabVisibilityContext` 多 `studioResumable`；`tabOffered` = open || resumable。条件仍然落在
「这个 agent 走过 AI 创建路径且没按完成」上，不是「所有 agent 都能开 studio」。

## 2026-09-03 (评审修订) — 可见性规则收口到注册表

`builder` 曾只在两个消费点被过滤（MainLayout 的切换器、ChatHeader 的硬编码菜单），
而 `ALL_TABS` 这条派生链没被扫到：⌘K 面板（移动端主入口）对任意 agent 都列出
Builder。现在 `AtomicTabDef.conditional?: 'studio'` 是**唯一**的规则所在，
`visibleCategories(ctx)` / `visibleTabs(ctx)` 是所有「可选列表」的出口 ——
切换器与 palette 都调它，新消费者自动继承。`ALL_TABS` / `tabLabelKey` /
`tabDescKey` 仍含 `builder`：注册表不过滤，否则已经停在这个 tab 上的抽屉标题会回
落成 `rail.builder` 字面量。测试 `builderTab.test.tsx` 钉住「关 studio 时
visibleTabs 恰好少这一个」。

## 2026-09-03 — 新增 `builder` 原子 tab

创建工作室的配置面板。放在 Config 组**首位** —— 它是配置的「从这里开始」：一段
把其余 tab 填好的对话。遵守既有 IA（一个 tab 一个 panel），没有嵌套堆叠。

## 2026-08-06 (2) — tabDescKey:每个面板一句话说明

新增 `tabDescKey(id)` → `rail.desc.<id>` 约定(11 个面板 × 10 语言)。
消费方:BookmarkDrawer 头部的 ? 圆圈(hover 气泡)、ChatHeader ⋯ 菜单项
的 title。目的:不看文档的用户也能知道 Awareness / Channels / MCP 等
是干什么的(Owner 2026-08-06)。文案讲「用处 + 使用逻辑」各一句,
新增面板时必须同步补 10 语言的 desc key。

## 2026-08-06 — artifacts 成为原子 tab + 自定义 glyph

新增 `artifacts` AtomicTabId(Activity 类目,Jobs/Inbox 之后),面板 =
ArtifactColumn(forceExpanded)走 BookmarkPanelHost。图标是本文件导出的
**ArtifactsGlyph**(圆点-连线-方块,Owner 2026-08-06 截图指定;lucide 无
此形,本地按 lucide 笔画约定手绘,cast 成 LucideIcon 供注册表/palette/
ChatHeader 混用)。deriveTabStatus 对 artifacts 走默认 none — 头部徽标
数来自 artifactStore,不经 bookmark 信号。

## 2026-06-20 — MCP strip caption shortened

The `mcp` tab gained `stripLabel: 'MCP'` so the strip caption reads "MCP"
instead of the truncated "MCP SERVE…". The full `label` ("MCP Servers") is kept
for the tooltip / aria-label.

## 2026-06-11 (PM)

`stripLabel` optional short caption for the 64px strip (Social
Network → "Social").



# tabs.ts — Atomic-tab registry (single source of truth)

Owner-decided IA (2026-06-11): tab ids, labels, icons, category
grouping, bookmarkStore-signal→status mapping (deriveTabStatus) and
the opened-clears-info rule (markTabOpened) all live here. The strip
and the panel host both consume this file; adding a tab = one entry
here + one render branch in [[BookmarkPanelHost]].

Status semantics per tab: jobs (failedJobs badge > running spinner >
info dot), inbox (unread badge), awareness (external-update info dot);
others currently 'none'. Attention/badges clear only when the
underlying condition resolves — markTabOpened clears info tiers only.
