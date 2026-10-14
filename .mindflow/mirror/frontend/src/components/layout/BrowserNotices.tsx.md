---
code_file: frontend/src/components/layout/BrowserNotices.tsx
last_verified: 2026-10-08
stub: false
---

# BrowserNotices.tsx — 把浏览器登录请求送到用户真正在的地方

挂在 app shell，不在浏览器面板里：agent 是在**聊天**里说"请完成登录"的，而面板未必打开；
请求若只在面板里出现，就是一个用户到不了的目的地（本功能里第三次犯同类错误后定下的位置）。
它跟随用户当前在聊的 agent、显示在最上层：一个卡在登录上的 agent 否则看起来像挂了。

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
