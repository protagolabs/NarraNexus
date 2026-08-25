---
code_file: frontend/src/lib/createAgentSkills.ts
last_verified: 2026-08-25
stub: false
---

# createAgentSkills.ts — 创建 Agent 时的 Skill 安装边界

## 为什么存在

Agent 创建后的后端 provisioning 会自动安装默认 Skills，而创建页只应显式安装用户额外选择的 Skills。这个文件把“最终展示为已包含”和“需要前端发起安装”两种集合语义分开，避免 UI 与后端并发安装同一个 Skill。

## 上下游关系

- **调用方**：[[../../pages/CreateAgentPage.tsx]] 用它合并默认项与用户选择，并生成需要逐个安装的非默认项。
- **数据形状**：复用 [[../types/skills.ts]] 的 Marketplace Skill 类型；默认项由 [[api.ts]] 从 marketplace defaults 端点读取。

## 设计决策

- 默认 Skills 总是排在用户选择之前，并按 `skill_id` 去重。
- HTTP 409 表达目标 Skill 已经安装，创建流程将其视为幂等成功；其他错误仍进入部分配置失败提示。

## Gotcha

默认 Skills 的真正安装责任仍在后端 provisioning。这里仅控制创建页展示与请求去重，不能替代后端安装。
