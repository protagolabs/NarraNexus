---
code_file: frontend/src/components/bookmarks/tabs.ts
last_verified: 2026-08-06
stub: false
---

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
