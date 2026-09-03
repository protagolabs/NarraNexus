---
code_file: frontend/src/components/builder/
last_verified: 2026-09-03
stub: false
---

# components/builder/ — Agent 创建工作室（v0）

## 这个目录做什么

三个组件 + 一个纯映射，构成「通过 AI 创建」这条路径上**唯一的新增 UI**：

| 文件 | 职责 |
|---|---|
| [[ProviderPickerModal.tsx]] | provider 准入闸门（设计里的方案 B 弹窗） |
| [[ApplyDraftBar.tsx]] | 草稿写进 Awareness 的唯一入口 |
| [[providerRows.ts]] | `/api/providers` 的松类型 map → 弹窗要画的行 |

## v0 为什么这么小

因为「右侧配置面板」在产品里已经有了。三个既有能力被直接复用，没有包装层：

- `register_artifact`（`common_tools_module`，`module_type="capability"`，
  **每个 agent 自动加载**）—— agent 自己把配置草稿写成 workspace 文件再注册
- [[ArtifactColumn.tsx]] —— 已经是布局第 4 列，新 artifact 到达时自动展开
- `text/markdown` artifact —— `kindRegistry` 里是 block-editor +
  防抖自动保存，用户能直接改草稿

所以这个目录里**没有**配置面板、没有 diff-apply hook、没有
`<agent_draft>` 协议。这些属于 v1（见
`reference/self_notebook/specs/2026-08-26-agent-creation-studio-design.md`）。

## 目录边界

对话本身不在这里 —— 它就是普通聊天页。指令的组装和剥离在
[[builderPrompt.ts]]，跨页面的一次性标记在 [[builderSession.ts]]，
入口页是 [[ChooseCreateMethodPage.tsx]]。
