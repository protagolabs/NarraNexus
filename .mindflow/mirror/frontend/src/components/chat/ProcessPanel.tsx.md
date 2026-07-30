---
code_file: frontend/src/components/chat/ProcessPanel.tsx
last_verified: 2026-07-30
stub: false
---

# ProcessPanel.tsx — 运行中的 terminal 风格过程面板

## 为什么存在

过程和回复原先按时间顺序混在同一条 TurnTimeline 里，靠实线/虚线左框
分层。信息不缺，但读的人要在噪音里找答案。分开之后气泡只有答案，过程
在这里连续滚动——像 terminal 一样**可以扫，不必读**。

## 设计决策

- **只在运行中挂载**（ChatPanel `isStreaming` 时）；结束即卸载，过程
  按回复切段折叠回各自气泡（segmentTurn）。所以这里不做任何持久化。
- **plan 钉在底部、不参与滚动**：它回答「现在到哪了」，不该被滚走。
  取 events 里最后一条 plan（store 对 plan 已是原地替换，最后一条即
  最新快照）。
- **pending 工具行**：名字 + 转圈 + 省略号——「名字已定、参数还在写」
  的诚实可见形态。
- **滚动跟随**：默认贴底；用户一旦上滚（距底 >24px）就不再抢视口，
  和消息区同一套取舍。

## Gotcha

- 面板只渲染 process 事件（thinking/tool_call/tool_output）+ plan；
  reply/native_output 属于气泡，在这里画就是重复渲染。
- 无过程且无 plan 时返回 null——空面板一个像素都不该占。
