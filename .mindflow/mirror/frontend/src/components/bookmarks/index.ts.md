---
code_file: frontend/src/components/bookmarks/index.ts
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — barrel 只再导出 `BuiltinTabId` 类型

`BUILTIN_TAB_IDS` 常量与注册表派生的 `builtinTabIds()` 同名异义，barrel 里并列会误导；常量由测试与 `platform/builtin.ts`
直接从 `./builtinTabIds` 取。

## 2026-09-07 — 再导出 `BUILTIN_TAB_IDS` / `BuiltinTabId`

供壳内置侧标窄类型。

## 2026-09-04 — 不再导出 `visibleCategories`（见 [[tabs]] 同日条）

## 2026-09-03 — 再导出 `visibleCategories` / `visibleTabs` / `TabVisibilityContext`

MainLayout 与 CommandPalette 的可选列表都从这里拿，见 [[tabs]] 09-03 条。

## 2026-08-06 — BookmarkStrip 退役

Chat UI v4 把面板入口移进聊天头部([[../chat/ChatHeader.tsx]]),
BookmarkStrip.tsx 与其测试删除,barrel 不再导出 BookmarkStrip /
STRIP_WIDTH_PX。tabs.ts 注册表保持单一事实源(header 从中取 id/图标/
label/deriveTabStatus/markTabOpened)。

## 2026-07-30 — also re-exports `STRIP_WIDTH_PX`

[[MainLayout]] needs the strip width to size the drawer's `edgeReservePx`,
and imports the whole bookmark family through this barrel.

# bookmarks/index.ts — Barrel for the bookmark family

Re-exports [[BookmarkStrip]], [[BookmarkDrawer]], [[BookmarkPanelHost]]
and the [[tabs]] registry. ActivityPanel/AgentProfilePanel removed in
the 2026-06-11 atomic-IA revision.
