---
code_file: frontend/src/components/help/helpContent.ts
last_verified: 2026-08-24
stub: false
---

## 2026-08-24 — Squads 锚点替换已删除的 agent-list / team-section

`sidebar.agent-list`("Agents, grouped by team")和 `sidebar.team-section`
("A team section")两条注释所指的 DOM 锚点(`AgentList.tsx` 里的
section 标签、`AgentGroupSection.tsx` 里的 team 行)随侧栏聊天列表一起删除
了,合并成一条指向新 `sidebar.squads` 锚点([[../layout/Sidebar.tsx]] 的
Squads 导航行)的注释。`sidebar.manage-agents` 的文案从 "Dashboard" 改成
"Agents",跟同一锚点现在的按钮文案对齐。见
[[../layout/Sidebar.tsx]] 2026-08-24 条目。

## 2026-08-06 — Chat UI v4 锚点跟随

侧栏改版后更新三条注释:`sidebar.create-agent` 现挂在 New 菜单上(文案改为
create or import);`sidebar.manage-agents` 挂在 Dashboard 导航行(manage
页并入 Dashboard);`sidebar.agents-menu` 注释改为 `sidebar.export`(⋯ 菜单
已删除,导出是一级导航行)。overlay 对缺失锚点静默跳过,无需强同步。

## 2026-06-11 (PM) — pages

Manifest restructured to HelpPage[] (3 Owner-specified topics). New
anchors: sidebar.manage-agents, sidebar.team-section, chat.messages,
layout.artifacts. `side` → `rail` (left/right/top note placement).

## 2026-06-11

Strip anchors re-pointed after the atomic-IA revision:
bookmarks.activity/bookmarks.agent → bookmarks.strip + bookmarks.jobs.



# helpContent.ts — Annotation manifests (pure data)

One exported array per view; entries reference `data-help-id` anchors.
Density discipline (spec §12.5): **≤ 8 per view**, enforced by a test —
a view that needs more annotations needs less UI, and this list doubles
as a complexity audit. Settings-page manifest deliberately deferred
until the parallel Settings redesign lands (spec §14.7). Used by
[[HelpButton]] / [[HelpOverlay]].
