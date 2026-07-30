---
code_file: frontend/src/lib/segmentTurn.ts
last_verified: 2026-07-30
stub: false
---

# segmentTurn.ts — 一轮事件按「用户可见片段」切段的纯函数

## 为什么存在

聊天区过程面板改造（2026-07-30）确立分工：过程在 composer 上方的
ProcessPanel 滚动，答案留在气泡。一轮天然是
`[思考, 工具, 工具, 回复₁, 思考, 工具, 回复₂, …]`——每个回复之前的
过程属于它。`segmentTurn` 在每个用户可见片段处切开，产出 `Segment[]`。

**一个函数，三条路径**：运行中的 `currentEvents`、刚结束的
message、刷新后 `/event-log` 的 timeline（经 `timelineToEvents` 归一）。
直播看到的和刷新后看到的必然一致——不是两份实现碰巧对上，而是同一份
实现。测试里专门有一条用例钉住这个等价性。

## 切段规则（每条都是判断而非显然默认）

- **末尾残留过程归最后一段**：说完最后一句还干了活，正是该被看见的。
- **零回复轮次**产出一个 `reply=null` 的段——过程不丢。
- **连续 native_output 合并**：是同一句话拆成的多个 delta；一旦中间
  夹了工具调用，就是又说了一次，开新段。合并条件含 `via===undefined`
  （走了表达工具的 reply 不和原生文本合并）。
- **plan 不属于任何段**：它是「现在到哪了」，由 ProcessPanel 底部
  固定区单独渲染。

## timelineToEvents

原先内联在 MessageBubble 且**故意跳过 reply**（回复文本已在
message.content 渲染过）。切段需要 reply 作切点，所以这里保留——
重复渲染改由「气泡只渲染 segment.reply」来避免。

## 上下游

- 型别 `Segment` / `SegmentReply` / `ProcessEvent` 定义在
  `types/messages.ts`（types 不反向依赖 lib）。
- 消费者：ChatPanel（直播）、chatStore.stopStreaming（存段）、
  MessageBubble/SegmentedReply（历史）。
