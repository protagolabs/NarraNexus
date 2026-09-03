---
code_file: frontend/src/hooks/useStudioTurn.ts
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 (评审修订) — 目录「未知」+ 错误有出口 + 状态走 store

- **目录 `null` 直到拉到为止**。原来 fetch 失败置 `[]`、且 effect 只依赖
  `[studioOpen]` 不重试，于是一次 marketplace 抖动让本次会话每一轮都把推荐过滤成
  空并存盘。现在 `loadCatalogue()` 在 encode / apply 两条路径按需懒加载、失败保持
  未知并在下一轮再试；未知时 [[../lib/builderProtocol.ts]] 的 merge 让 `skill_ids`
  整体回落。「已加载且为空」仍拒掉所有 id。
- **写失败进 `studioStore.applyError[agentId]`**，[[../components/builder/BuilderConfigPanel.tsx]]
  与它自己的手改错误合成同一条提示行 —— `builderApply.ts` 注释里「surfaced in the
  panel」这句话现在是真的。不弹 modal、不禁用 composer（铁律 #15）。
- 推荐通过 `setRecommendations` 写 store，只推荐 skill 的一轮也能让面板重渲染，
  不需要无条件 `refreshAgents()`（那会让什么都没变的一轮多打两个请求）。
- 删掉从未被读的 `catalogue` state 与从未被消费的三个返回值；hook 只返回
  `{ encodeOutgoing, applyFromReply }`。`CATALOGUE_LIMIT` 是页大小不是静默上限：
  信封会告诉模型「first N of M」。

# useStudioTurn.ts — studio 插进普通聊天回合的两个点

## 为什么存在

[[ChatPanel.tsx]] 是 ~1400 行承重的流式逻辑。studio 只通过两个调用碰它 ——
发送前 `encodeOutgoing`，回合落定后 `applyFromReply`，其余全在这里。

## 关键决策

- **落定沿触发，不是监听消息列表**：`<agent_draft>` 块只有在回合结束后才完整，
  流式中途应用会把半序列化的 JSON 值写进 agent 的指令里。读的也是 store 里
  **已落定**的消息，不是流式缓冲。
- **skill 目录只在 studio 打开时拉**。它存在的唯一目的是让
  `mergeAgentDraft` 能拒掉不存在的 id，以及让面板能按 id 安装。拉失败时目录为
  **未知**（见上方 09-03 修订），不是「空」。
- **只刷新真正变了的东西**：identity 变了才 `refreshAgents`，awareness 变了才
  `refreshAwareness`。
- `encodeOutgoing` 任何失败都返回原文 —— studio 出问题不能把用户刚写的消息吞掉。

## 上游 / 下游

协议在 [[builderProtocol.ts]]，写入在 [[builderApply.ts]]，开关在
[[builderSession.ts]]，面板在 [[BuilderConfigPanel.tsx]]。
