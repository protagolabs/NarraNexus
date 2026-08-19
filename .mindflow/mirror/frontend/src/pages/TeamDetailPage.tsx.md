---
code_file: frontend/src/pages/TeamDetailPage.tsx
last_verified: 2026-08-19
stub: true
---

> 本 mirror 是补建的最小条目(此前该文件无 mirror);只覆盖下述改动,
> 页面整体职责待补写。

## 2026-08-19 — Edit team 带上自己的 teamId

`TeamManagementModal` 挂载点补 `initialTeamId={teamId}`(useParams)。
此前 modal 无上下文入口,兜底选 `teams[0]`——团队 #5 的详情页点 Edit team
编辑的是团队 #1,与 Dashboard Teams 行的同类缺陷一起修(见
[[TeamManagementModal]] 08-19 条)。
