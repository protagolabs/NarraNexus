---
code_file: frontend/src/lib/builderPrompt.ts
last_verified: 2026-09-03
stub: false
---

# builderPrompt.ts — 创建工作室 v0 的指令载体与草稿约定

## 为什么存在

v0 的对话跑在**刚创建出来的那个 agent 本人**身上，没有隐藏 carrier。于是
Builder 的行为没有地方注入：Awareness 是用户的东西，v0 只在用户点
[[ApplyDraftBar.tsx]] 时才写它。指令因此**随第一条用户消息走**，用方括号
标记包住，由 [[MessageBubble.tsx]] 在渲染前剥掉。

标记用方括号而非 XML 风格，是为了不和模型在普通 Markdown 里吐出的标签撞车。

## 为什么不能在进入时就发指令

指令的作用是**框住用户自己那句需求**，而那句话在用户敲之前并不存在。所以
[[ChooseCreateMethodPage.tsx]] 只打一个标记（[[builderSession.ts]]），
真正的包裹发生在 [[ChatPanel.tsx]] 的提交路径上。

## 关键决策：只认一个 artifact 标题

指令里写死 `agent-config` 这个标题，`isConfigDraft` 也只认它。模型不守约定时
的后果是**「应用」按钮不出现**，用户手动复制一次；反过来放宽匹配，就会在
agent 恰好产出的任何 Markdown 上挂一个「写进你的指令」按钮。失败方向选在
「不方便」而不是「写错」。

`isConfigDraft` 放在这里而不是挨着按钮，有两个原因：标题和判定必须一起改；
组件文件导出非组件会破坏 fast refresh（eslint `react-refresh` 会报错）。

## Gotcha

- `stripBuilderInstruction` **必须有两条正则**：闭合形态之外还要兜未闭合形态。
  我们自己不产出未闭合的块，但半个标记泄漏出去就是整段 prompt 出现在用户的
  气泡里 —— 代价不对称，所以兜住。
- 指令里的 `register_artifact` / `target_artifact_id` 字样是和
  `common_tools_module` 的既有工具对齐的**契约**，改工具名要同步改这里。
