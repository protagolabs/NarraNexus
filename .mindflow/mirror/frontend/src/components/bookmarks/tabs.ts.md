---
code_file: frontend/src/components/bookmarks/tabs.ts
last_verified: 2026-09-03
stub: false
---

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
