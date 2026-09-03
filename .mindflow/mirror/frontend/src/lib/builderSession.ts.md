---
code_file: frontend/src/lib/builderSession.ts
last_verified: 2026-09-03
stub: false
---

# builderSession.ts — 「下一条消息带 Builder 指令」这一个标记

## 为什么存在

创建工作室 v0 的指令必须包住用户**自己打的第一句话**（见
[[builderPrompt.ts]]），所以不能在进入聊天页时就发出去。
[[ChooseCreateMethodPage.tsx]] 建完 agent 后打一个标记，
[[ChatPanel.tsx]] 的提交路径消费它一次。

## 为什么用 sessionStorage

不是路由 state，也不是 store 字段：

- **比路由 state 活得久**：刷新页面标记还在，路由 state 会丢。
- **按标签页隔离**：两个标签页各开一个创建流程，不会互相抢标记。
- **不用改 chatStore**：v0 的全部意义就是不碰承重路径。

## 关键决策：consume-once

`takeBuilderPending` 读完即清。标记若泄漏到后续回合，用户每发一条消息都会
重新携带整段指令 —— 既烧 context，又在反复教一个已经被教过的模型。

## Gotcha

- 私密模式 Safari 访问 `sessionStorage` 会抛，SSR / 测试环境可能没有
  `window`，所以取用包在 try 里，拿不到就整体降级为「没有标记」。
- 空 `agentId` 在三个方向上都是惰性的，不会写出 `nn.builderPending.` 这种
  没有主键的垃圾键。
