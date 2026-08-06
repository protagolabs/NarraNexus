---
code_file: frontend/src/pages/CreateTeamPage.tsx
last_verified: 2026-08-06
stub: false
---

# CreateTeamPage — v4 团队创建独立页

## 为什么存在

Chat UI v4 把「创建团队」从 TeamManagementModal 的左栏提升为整页
(侧栏 New 菜单入口):name / description / color 色板(与 modal 同一组
COLOR_PRESETS,视觉词汇一致)/ agent 多选清单 / Create。管理既有团队
(重命名、intro、成员、删除)不在这里 — 页脚提示去 Dashboard Teams 视图。

## 设计决策

- **成员选择延迟提交**:勾选只改本地 Set;Create 时先
  teamsStore.createTeam({name, description, color})拿 team_id,再逐个
  addMember(逐行 best-effort,失败聚合进 useNotice 弹窗 — 绝不
  window.alert,wry 会吞掉)。这修正了 modal 每次点击立刻打 API 的模式。
- description 首次真正随建团发送(modal 从来只发 {name, color})。
- 成功后直接 navigate 进新团队的群聊,让结果立即可见。
