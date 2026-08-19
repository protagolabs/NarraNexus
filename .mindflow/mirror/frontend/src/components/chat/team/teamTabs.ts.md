---
code_file: frontend/src/components/chat/team/teamTabs.ts
last_verified: 2026-08-19
stub: false
---

## 2026-08-19(二)— 注册表变 builder(带活计数)

`teamDrawerCategories({members,artifacts,files})` 返回带 count 的注册表
(共享文件的数量此前无处可见);`teamTabLabelKey` 用显式映射表,不再依赖
「第一个 category」的下标假设。

# teamTabs — 团队房间的抽屉面板注册表

members(roster)/artifacts/files 三项,喂给 [[../../bookmarks/BookmarkDrawer]]
的 switcherCategories。「team 右侧=单聊右侧同一逻辑」的差异只剩这份清单。
labelKey 复用既有 roster.title/workspace.tab*;分组名 chat.team.drawerCategory
(×10)。加面板=在这里加一行。
