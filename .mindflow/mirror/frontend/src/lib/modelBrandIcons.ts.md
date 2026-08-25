---
code_file: frontend/src/lib/modelBrandIcons.ts
last_verified: 2026-08-20
stub: false
---

# modelBrandIcons.ts — protocol/model-id → brand icon matching

## 为什么存在

[[../components/icons/ModelBrandIcons.tsx]] 只放 React 组件（react-refresh
的 `only-export-components` 规则不让一个文件混合导出组件和普通函数），
这两个纯匹配函数拆到这里。

## 两个函数覆盖的场景不一样

- `getProtocolBrandIcon(protocol)` —— 给 **Provider** 下拉用。
  `ProviderSummary.protocol`（见 [[../lib/agentFramework.ts]]）全仓库
  只有 `'anthropic' | 'openai'` 两个值，所以只有两个分支，直接
  Claude / OpenAI 图标，不用管 `source`（netmind / yunwu / openrouter /
  oauth 之类的 8 种来源）——来源信息下拉文字里已经写了
  （"NetMind (Anthropic)" 这种），图标只需要传达底层模型家族。
- `getModelBrandIcon(modelId)` —— 给 **Model** 下拉用。按 model_id
  里的关键字子串匹配（小写化后 `includes`），覆盖
  `agentFramework.ts` 的 `MODEL_SUGGESTION_GROUPS` 全部 8 个厂商分组。
  自定义 base_url provider 的 model_id 可能是任意字符串，匹配不上就
  `null`——调用方（`IconSelect` in `CreateAgentPage.tsx`）对 `null` 就
  不渲染图标位，不是报错也不是留空洞占位。

## Gotcha

`o[0-9]` 前缀判断（`/^o[0-9]/.test(id)`）是给 OpenAI 的 o3/o4-mini 这类
"o 开头数字"命名兜底的——纯 `includes('gpt')` 抓不到这些。如果 OpenAI
以后出新的命名系列，这条正则可能需要跟着扩。
