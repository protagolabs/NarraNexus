---
code_file: frontend/src/components/teams/TeamProfileForm.tsx
last_verified: 2026-09-03
stub: false
---

# TeamProfileForm — 团队资料表单（名称 / 颜色 / 简介 + 一个保存）

## 2026-09-03 — 从 TeamManagementModal 抽出

只有三个字段和一个 Save,**没有**删除、切换团队、新建团队。抽出的原因:房间的团队管理 tab
要提供「编辑资料」,而整个团队管理弹窗左栏可切任意团队/建团队、其删除不会带房间离开
(auto-review I9)。两处共用同一个表单:[[TeamManagementModal]](`trailing` 槽放它的删除按钮)
与 [[../chat/team/TeamManagePanel]](内联,无 trailing)。`onSave(patch)` 只带
`{name,color,intro_md}`,**永不带 `lead_agent_id`**。草稿按 `team_id`/`updated_at` 重新播种,
服务端更新的名称不会被这里的旧草稿盖回去。守卫:`__tests__/teamProfileForm.test.tsx`。
