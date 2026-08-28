---
code_file: frontend/src/components/dashboard/AgentModelCard.tsx
last_verified: 2026-08-26
stub: false
---

## 2026-08-26 — Dashboard 展开行的模型配置卡

只读展示单 agent 两个槽（agent / helper_llm）的 effective 模型 + reasoning +
inherit/override 徽章，右上「编辑」按钮通过 `onEdit` 让 [[DashboardPage]] 打开
共享的 [[AgentLlmConfigPanel]]。数据走既有 `GET /api/agents/{id}/llm-config`，
**懒加载**（组件随展开行挂载才拉），`reloadKey` 变化（编辑保存后）重新拉取。
本身不写任何配置——写操作全在 AgentLlmConfigPanel 里。

**2026-08-27**：所有文案（title/agentSlot/helperSlot/inherit/override）均走注册
的 i18n key（en + zh 全量），不再有裸英文字面量。effort 整句进单个插值 key
`modelCard.effort`（`effort={{value}}`），等号不再落在 key 外。
