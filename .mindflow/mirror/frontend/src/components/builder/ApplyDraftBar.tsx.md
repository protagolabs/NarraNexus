---
code_file: frontend/src/components/builder/ApplyDraftBar.tsx
last_verified: 2026-09-03
stub: false
---

# ApplyDraftBar.tsx — 草稿变成真指令的唯一入口

## 为什么存在

v0 **从不代替 agent 写 Awareness**。草稿活在 artifact 里，按下这个按钮之前
一直是惰性的。这是产品规则不是能力缺口：对话跑在用户自己的 agent 上，自动写入
等于「因为一个模型提了建议，我们就改了你的配置」。

## 为什么只认一个标题

只在 [[builderPrompt.ts]] 里 `isConfigDraft` 认可的 artifact 上出现。模型不守
约定时用户手动复制一次；放宽匹配则会在 agent 恰好产出的任何 Markdown 上挂一个
「写进你的指令」按钮。失败方向选在「不方便」而不是「写错」。

## 关键决策：编辑器脏的时候禁用

按钮读的是 artifact 在服务端的字节（raw URL），**不是编辑器缓冲区**。所以在
防抖窗口内点下去，写进去的会是用户最后一次编辑**之前**的版本。等自动保存
（失焦 + 空闲）落地是诚实的行为；声称应用了我们没读到的编辑不是。
脏状态来自 [[artifactStore]] 的 `editorDirtyIds`，由
[[MarkdownRenderer.tsx]] 写入。

覆盖指令在 UI 上不可撤销，所以即便草稿正是用户来应用的东西，也走一次确认。

## Gotcha

应用成功后的提示必须说清**只写了指令** —— 名称、Skills、渠道 v0 不代做，
用户得自己去面板配。不说清就等于让用户以为 agent 已经配全了。
