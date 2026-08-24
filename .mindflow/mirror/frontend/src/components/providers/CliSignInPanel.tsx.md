---
code_file: frontend/src/components/providers/CliSignInPanel.tsx
last_verified: 2026-08-24
stub: false
---

## 为什么存在

这个组件是从 [[ProviderSettings]] 的 "oauth" 标签页里机械抽取出来的独立组
件：Claude Code Login 卡片（OS 凭证态 + provider 记录态 + setup-token 粘贴
流程）和 Codex CLI Login 卡片（同样的两层态，但没有 Tauri 自动化，只有终端
提示）。**注意**：这次抽取只是把渲染逻辑和状态搬进新文件，并让它在独立场
景下可运行、可测；把 `ProviderSettings.tsx` 里那段原地渲染换成
`<CliSignInPanel />` 调用是后续任务（计划里的 Task 7）的工作，本文件对应
的这次改动**尚未**触碰 `ProviderSettings.tsx`。

直接动机同 [[CustomEndpointForm]]：即将新增的 Create Agent 向导需要一个
"CLI 登录" 的添加方式（Claude Code / Codex CLI 的 OAuth 登录），跟 Settings
页面现有的 oauth 标签页是同一套逻辑（登录/重新登录/登出、状态轮询、倒计
时、setup-token 粘贴、"Add as Provider"）。抽成独立组件后,Settings 和向导
的 provider 步骤可以共享同一份实现,而不是两处各自维护一份几乎相同但逐渐
漂移的登录 UI。

组件把 `providers` 作为 prop 接收（而不是自己去 fetch 整个 provider 列
表）——调用方把自己 state 里 provider 钱包的一个子集（只需要 `source` +
`auth_type` 两个字段）传进来，本组件纯粹从这个子集派生"是否已经添加过"
（`hasClaude` / `claudeTokenConnected` / `hasCodex`），不持有、也不拥有权
威的 provider 列表。这样 Settings 页面（拥有完整 provider 列表 + 弹窗）和
Create Agent 向导（可能只关心这一步刚添加的 provider）都能直接复用，不需
要额外做数据形状适配。

## 依赖

- `providerApi.ts`（`addProvider` / `fetchClaudeStatus` / `fetchCodexStatus`）
  ——网络请求全部走这三个函数,组件本身不直接 `fetch`。`addProvider` 返回
  `{ ok, detail? }`；`fetchClaudeStatus` / `fetchCodexStatus` 各自返回
  `ProviderCliStatus | null`（`null` 代表请求失败,组件保留上一次已知状态,
  不覆盖成空）。
- `@/lib/tauri`（`isTauri` / `triggerClaudeLogin` / `triggerClaudeLogout` /
  `cancelClaudeLogin`）——桌面端专用的 Claude 登录自动化入口,只在
  `isTauri()` 为真时渲染登录/登出按钮；Codex 没有对应的 Tauri 触发器（见下
  "行为要点"）。

## 行为要点

- 挂载时并发拉取 `fetchClaudeStatus()` + `fetchCodexStatus()`（一次性
  effect,空依赖数组）；调用方若需要重新拉取（比如向导切换步骤后回来）,
  目前只能靠重新挂载组件——组件没有暴露手动 refresh 的 prop,和原
  `ProviderSettings.tsx` 里 `refreshConfig` 被其它多个地方复用的情况不同,
  这里没有那个复用面,所以没做成受控。
- 倒计时 `useEffect`（`claudeLoginRemaining`）逐秒递减,到 0 时调用
  `cancelClaudeLogin()` 让 Rust 侧 SIGTERM 挂起的 `claude auth login` 子进
  程,但**不**在这里把 `claudeLoginRemaining` 置回 `null`——`handleClaudeLogin`
  的 `finally` 块会在 `triggerClaudeLogin()` 的 await 真正返回（进程真正退
  出）之后才清空,避免计时器提前归零导致状态和真实进程状态不同步。
- `handleAddClaudeOAuth` / `handleSaveSetupToken` / `handleAddCodexOAuth`
  三个 handler 都只在 `addProvider()` 返回 `{ ok: true }` 时才调用
  `onComplete()`——失败时组件目前**不展示行内错误**（这点和
  `CustomEndpointForm` 不同,后者有自己的 `error` state）。这不是遗漏：原
  `ProviderSettings.tsx` 里 oauth 标签页的错误也是走组件级共享的 `error`
  state 展示在整个弹窗底部,而不是卡片内部;抽出来的这个组件没有"弹窗底部"
  这个概念,错误展示的位置留给调用方决定（调用方可以检查 `addProvider` 的
  返回值自己处理,但当前这个组件不做,因为调用方目前唯一的用法——本次抽
  取——还没有接线,错误 UX 留给 Task 7/10 wiring 时按实际调用场景决定）。
- Codex 卡片**没有** Tauri 自动化,永远显示"去终端跑 `codex login`"的提
  示——这是刻意保留的原始行为,不是这次抽取漏做的功能,`codex login` 会打开
  浏览器,目前没有走 Tauri IPC 的封装。
- `providers` prop 只读两个字段（`source` / `auth_type`）,调用方传入的对
  象即使带其它字段（比如完整的 `ProviderSummary`）也兼容——组件不会因为
  多余字段报错,只是忽略。
