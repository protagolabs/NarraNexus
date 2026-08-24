---
code_file: frontend/src/components/providers/ModelBubbleInput.tsx
last_verified: 2026-08-24
stub: false
---

## 2026-08-24 — 从 ProviderSettings.tsx 机械抽取

`ModelBubbleInput` / `ModelSuggestionChips` 原来是 [[ProviderSettings]] 内部的
私有组件，提交 `22e59e21` 把它们原样搬到这个独立文件，纯粹是**机械抽取**——
没有改行为、没有改 props 形状。

## 为什么存在

抽取的直接动机是复用：即将新增的 `CustomEndpointForm.tsx`（后续任务）需要
同一套 tag 风格的 model-id 输入控件——手输一个 model id 回车/点 `+` 生成一
个 chip，chip 可删除，还能从 `suggestions` 里点选补全。如果不抽出来，
`CustomEndpointForm` 就得整段复制 ProviderSettings.tsx 里这 ~120 行渲染逻辑
（tag 列表 + 输入框 + pending-hint 警示 + suggestion chips），两处各自漂移。
现在两个调用方（Settings 内的自定义端点表单 / 编辑模型弹窗，和 Create Agent
向导的 provider 步骤）共享同一份实现。

组件本身是**纯组件、无副作用**：

- **入参**：`models: string[]`（当前已提交的 model id 列表）、
  `onChange: (m: string[]) => void`（提交/删除时把新数组交还给调用方）、
  `placeholder?: string`（缺省走 i18n key
  `settings.provider.modelNamePlaceholder`）、
  `suggestions?: ModelSuggestionGroup[]`（来自 [[agentFramework]] 的分组建
  议，渲染成可点选的虚线 chip）。
- 不发网络请求、不读全局 store、不落地任何状态到组件外——所有状态（输入框
  文本 `input`）都是本地 `useState`，`models` 数组本身由调用方持有并通过
  props 传入/传出。这使它可以被任何"我需要一个 model 列表编辑器"的表单直接
  嵌入,不用担心跨表单状态污染。

`ModelSuggestionChips` 单独导出是因为它也被 provider 详情弹窗单独复用过（不
经过 `ModelBubbleInput` 的输入框那一半）。

## Gotchas

- **commit trap**——输入框里打的文字只有在按 Enter 或点击 `+` 按钮
  （`addModel`）时才会被 push 进调用方的 `models` 数组，逐字符 keystroke
  不会同步。如果调用方在用户打字过程中读取 `models`（例如提交表单前不检查
  `input` 是否还有未提交文本），会漏掉这个"挂起"的值——原始症状：用户在
  Add Provider 表单里敲了模型名但没按 Enter 就点了外层的提交按钮，模型名
  被静默丢弃，后端转而套用默认模型列表。组件自身对此做了可见性提示（不是
  修复）：`hasPending`（`input.trim().length > 0`）时输入框描边变
  `--color-warning`、`+` 按钮加 `animate-pulse`、下方渲染一行
  `pendingHint` 文案。真正的"提交前自动 flush"修复仍未做——调用方仍需自行
  在提交前检查未提交文本（这是从 ProviderSettings.tsx.md 移过来的历史
  gotcha，此前那份文档把它记在调用方那一节；现在行为的落脚点在这个组件
  里，记录挪到这里）。
- `suggestions` 传入的分组会先按 `models`（已选中的）过滤掉重复项
  （`visibleGroups`），再渲染；一个分组过滤完为空就整组不渲染，全部分组为
  空则 `ModelSuggestionChips` 返回 `null`——调用方不需要自己做这层去重。
