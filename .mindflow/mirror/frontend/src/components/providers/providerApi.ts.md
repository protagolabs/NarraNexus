---
code_file: frontend/src/components/providers/providerApi.ts
last_verified: 2026-08-24
stub: false
---

## 为什么存在

这是从 [[../settings/ProviderSettings]] 里抽出来的共享 fetch/mutation
层——`authFetch` / `providerUrl` / `addProvider` / `testProviderConfig` /
`fetchClaudeStatus` / `fetchCodexStatus`。抽取之前这几个函数（尤其是
`authFetch` 和 `providerUrl`）是 `ProviderSettings.tsx` 内部的模块级私有
函数，`CustomEndpointForm.tsx` / `CliSignInPanel.tsx`（本计划 Task 3/4 从
`ProviderSettings.tsx` 里抽出的两个独立组件）以及后来的
[[../../pages/CreateAgentPage]] provider 步骤都需要打同一批
`/api/providers/*` 接口——如果每个组件各自 `fetch`，identity header 的拼
装逻辑（见下）迟早会有一份漂移、某个组件忘了发 `X-User-Id` 或忘了发
JWT，复现一次 2026-05-18 那次"local 模式写错用户"的事故只是时间问题。抽
成单一文件后，"provider 相关请求怎么发"只有一处实现，四个调用方
（`ProviderSettings`、`CustomEndpointForm`、`CliSignInPanel`、
`CreateAgentPage`）全部通过它间接命中同一套逻辑。

## 依赖它的调用方

- [[../settings/ProviderSettings]] — provider 列表/详情/删除/测试/
  edit-models 都走这里的 `authFetch`/`providerUrl`。
- [[CustomEndpointForm]] — 自定义 endpoint 表单的 `addProvider` /
  `testProviderConfig`。
- [[CliSignInPanel]] — Claude/Codex CLI 登录卡的 `addProvider` /
  `fetchClaudeStatus` / `fetchCodexStatus`。
- [[../../pages/CreateAgentPage]] Step 1（provider 选择/添加步骤）——间接
  依赖：本身不直接 import 这个文件，而是通过复用
  `CustomEndpointForm`/`CliSignInPanel` 两个组件间接落到同一套网络层上。

## 设计要点

- **纯 `fetch`，零 React**——这是 `components/providers/` 目录下唯一一个
  `.ts`（不是 `.tsx`）文件，没有任何 JSX、没有 hook、没有组件导出。目录里
  其余文件（`ModelBubbleInput.tsx`/`CustomEndpointForm.tsx`/
  `CliSignInPanel.tsx`）都是渲染组件，只有这个文件是纯数据层，供它们内部
  调用。
- `authFetch` 的 identity header 逻辑跟历史上 `ProviderSettings.tsx` 内部
  版本完全一致（这是机械搬迁，不是重写）：从 `localStorage` 的
  `narra-nexus-config` 读 `state.token`（cloud 模式 JWT，塞进
  `Authorization: Bearer`) 和 `state.userId`（local 模式，塞进
  `X-User-Id`），**两个 header 都发**——后端按当前模式认对应的那个、忽略
  另一个（cloud 环境不会信任客户端自报的 `X-User-Id`，属于纵深防御）。见
  [[../settings/ProviderSettings]] 2026-05-18 条目：`X-User-Id` 缺失曾经
  触发后端"users 表第一行"兜底，把 API key 写到了错的账户上——那次事故之
  后后端已经关掉这个兜底，前端这边持续两个 header 都发是配合这个约定。
- `providerUrl(path)` 只走 header 认身份，不再走 `?user_id=...` 这种
  query-string 通道（同一次 2026-05-18 修复的一部分）。
- `addProvider` / `testProviderConfig` 都不管调用方的后续动作——`addProvider`
  成功后调用方自己决定要不要 `refreshConfig()`/`onComplete()`；
  `testProviderConfig` 是纯连通性探测，不落库，用于表单提交前的"Test
  connection"按钮。两者的返回形状分别是 `{ ok, detail? }` 和
  `{ ok, msg? }`，网络异常/JSON 解析失败都吞掉转成 `{ ok: false }`，调用方
  不需要自己套 try/catch。
- `fetchClaudeStatus` / `fetchCodexStatus` 请求失败返回 `null`（不是抛异
  常）——[[CliSignInPanel]] 依赖这个约定：拿到 `null` 时保留上一次已知状
  态，不会把"网络抖了一下"误渲染成"CLI 未登录"。
