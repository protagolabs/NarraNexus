---
code_file: frontend/src/components/providers/CustomEndpointForm.tsx
last_verified: 2026-08-24
stub: false
---

## 为什么存在

这个组件是从 [[ProviderSettings]] 的 "custom" 标签页（原始的 protocol +
endpoint 自定义表单：选协议 anthropic/openai → 填 name/auth_type/base_url/
api_key/models → test connection → submit）里机械抽取出来的独立组件。**注
意**：这次抽取只是把渲染逻辑和状态搬进新文件，并让它在独立场景下可运行、
可测；把 `ProviderSettings.tsx` 里那段原地渲染换成 `<CustomEndpointForm />`
调用是后续任务（计划里的 Task 5）的工作，本文件对应的这次改动**尚未**触碰
`ProviderSettings.tsx`。

直接动机同 [[ModelBubbleInput]]：即将新增的 Create Agent 向导需要一个
"自建/第三方 endpoint" 的添加方式，跟 Settings 页面现有的自定义端点表单是
同一套逻辑（选协议、填 base_url/api_key、测连接、保存）。抽成独立组件后，
Settings 和向导的 provider 步骤可以共享同一份实现,而不是两处各自维护一份
几乎相同但逐渐漂移的表单代码。

组件自己持有一份**独立的** `error` 状态，这一点和原来内联在
`ProviderSettings.tsx` 里的版本不同——原版本里，"custom" 标签页、"API key"
标签页、"CLI 登录" 标签页共享同一个组件级 `error` state（`ProviderSettings`
里的 `const [error, setError] = useState('')`，在 `handleAddProtocol` /
`handleAddOneKey` / CLI 相关 handler 里都会 set），三种添加方式的报错文案会
相互覆盖、且清空时机不完全对齐三个 tab 各自的生命周期。拆出来之后，每个
add-method 组件（已有的 `OneKeyOnboard.tsx`，以及这个 `CustomEndpointForm`）
各自拥有自己的错误状态和自己的清空时机（切换协议 `openProtocol` 时清空、
提交成功清空、提交失败时设置），互不干扰。这个约定与 `OneKeyOnboard.tsx`
现有的做法一致。

## 依赖

- `providerApi.ts`（`addProvider` / `testProviderConfig`）——网络请求全部走
  这两个函数，组件本身不直接 `fetch`。`addProvider` 返回
  `{ ok, detail? }`，失败时 `detail` 是后端返回的错误详情，直接渲染给用户；
  `testProviderConfig` 返回 `{ ok, msg? }`，是纯探测、不落库,用于 "Test
  connection" 按钮。
- [[ModelBubbleInput]]——"Available Models" 字段的 tag 输入控件，带
  `MODEL_SUGGESTION_GROUPS`（来自 `@/lib/agentFramework`）建议 chip。

## 行为要点

- 协议下拉框（`aria-label` 绑定 `settings.provider.protocolLabel`,方便测试
  用 `getByRole('combobox', { name: /protocol/i })` 定位）选中前，下方表单
  完全不渲染；选中后 `openProtocol` 会重置 name/url/key/auth/models/error/
  testResult 并把 base URL 预填成协议默认值
  （`https://api.anthropic.com` / `https://api.openai.com/v1`）。
- 修改 url/key/auth/models 中任意一项都会清空 `testResult`——测试结果只对
  当时的那组字段值有效,字段一变就必须重新测。
- 提交（`handleSubmit`）成功后组件把自己的表单状态整体复位（回到"未选协
  议"）并调用 `onComplete()`；调用方决定 `onComplete` 里做什么（关闭弹窗、
  刷新 provider 列表等）——本组件不管这些。
- 提交/测试前都要求 `key.trim()` 非空,否则设置
  `settings.provider.enterApiKeyShort` 错误文案并直接 return,不发请求。
