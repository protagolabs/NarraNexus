---
code_file: frontend/src/components/layout/BrowserNotices.tsx
last_verified: 2026-10-14
stub: false
---

# BrowserNotices.tsx — 把浏览器登录请求送到用户真正在的地方

挂在 app shell，不在浏览器面板里：agent 是在**聊天**里说"请完成登录"的，而面板未必打开；
请求若只在面板里出现，就是一个用户到不了的目的地（本功能里第三次犯同类错误后定下的位置）。
它跟随用户当前在聊的 agent、显示在最上层：一个卡在登录上的 agent 否则看起来像挂了。

## 2026-10-14 — no polling until the plugin boot pass has settled

The Browser panel is registered unconditionally at import; only the factory listing (`loadPlugins` ->
`disableBuiltinUi('builtin.browser')`) removes it where the distribution excludes the feature (cloud). `BrowserNotices` mounted
and polled in the window before that pass finished, so a cloud user got a guaranteed 404 and the red "could not check for browser
login requests" banner. It now also waits on `pluginsBootSettled()` (same external store `App.tsx` uses for plugin deep links).
A 404 from `GET /api/browser/notices` is read as "this server never mounted the feature": polling stops for that agent and no
banner shows (decided on `ApiError.status`, never message text; 5xx/network keep the visible retry banner). It covers the case where
the factory listing failed (5xx/timeout/expired token) so the panel was never removed. The route's owner check can also 404 an
agent the caller does not own; stopping the poll there is correct too (nothing to show), and an agent switch remounts and re-asks.
Gotcha: any test that renders `BrowserNotices` must settle a boot pass first (`loadPlugins` with a failing fetch is enough).

## 行为

- 轮询 `GET /api/browser/notices/{agent_id}`（请求由 MCP host 里的 agent 轮次发起，不在这个 tab
  的任何 socket 上）；结算后才排下一次，慢请求不会重叠；卸载后停止。
- 按 agent 用 keyed 子组件隔离：切换 agent 立即隐藏旧通知，上一个 agent 迟到的响应/错误不能
  覆盖当前的。响应里属于别的 agent 的条目不显示。
- 每条交给 `BrowserLoginNotice` 渲染（打开浏览器面板，不接管控制、不当作任何授权）；
  服务端移除后才消失，接管期间仍可见。
- 轮询失败：清掉旧通知、显示可重试的错误（`browser.notices.*`）。
- 浏览器插件被禁用（PANELS 里没有 browser）时不渲染也不轮询。

## 2026-10-08（PR #410 review I1）

原名 `BrowserApprovalNotice`，还渲染"网站能力审批"提示（`BrowserApprovalPrompt`）。审批从未被
任何代码路径发起，整条链路删除；组件改名、只保留真正在用的登录通知，接口从 `/approvals` 改为
`/notices`，文案改为"浏览器登录请求"（10 种语言）。
