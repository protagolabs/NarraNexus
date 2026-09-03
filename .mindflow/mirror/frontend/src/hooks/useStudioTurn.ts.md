---
code_file: frontend/src/hooks/useStudioTurn.ts
last_verified: 2026-09-03
stub: false
---

# useStudioTurn.ts — studio 插进普通聊天回合的两个点

## 为什么存在

[[ChatPanel.tsx]] 是 ~1400 行承重的流式逻辑。studio 只通过两个调用碰它 ——
发送前 `encodeOutgoing`，回合落定后 `applyFromReply`，其余全在这里。

## 关键决策

- **落定沿触发，不是监听消息列表**：`<agent_draft>` 块只有在回合结束后才完整，
  流式中途应用会把半序列化的 JSON 值写进 agent 的指令里。读的也是 store 里
  **已落定**的消息，不是流式缓冲。
- **skill 目录只在 studio 打开时拉一次**。它存在的唯一目的是让
  `mergeAgentDraft` 能拒掉不存在的 id，以及让面板能按 id 安装。拉失败时目录为
  空 → 所有推荐被拒 → 文本字段照常工作，这是安全的方向。
- **只刷新真正变了的东西**：identity 变了才 `refreshAgents`，awareness 变了才
  `refreshAwareness`。
- `encodeOutgoing` 任何失败都返回原文 —— studio 出问题不能把用户刚写的消息吞掉。

## 上游 / 下游

协议在 [[builderProtocol.ts]]，写入在 [[builderApply.ts]]，开关在
[[builderSession.ts]]，面板在 [[BuilderConfigPanel.tsx]]。
