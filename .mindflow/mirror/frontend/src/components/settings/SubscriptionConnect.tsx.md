---
code_file: frontend/src/components/settings/SubscriptionConnect.tsx
last_verified: 2026-08-28
stub: false
---

# SubscriptionConnect.tsx — Claude Code / Codex 订阅连接卡(从 ProviderSettings 抽出)

## 为什么存在

P0(2026-08-28):landing 只配订阅走不通。订阅登录原本埋在三层之下
(SetupPage 的 Advanced 折叠 → ProviderSettings 的 add modal → Sign in
tab),主卡 OneKeyOnboard 又是纯 API key 表单——订阅用户把 landing 读成
"必须有 API Key"。抽出后 SetupPage 把它放在**折叠区展开后的首位**(第一版做成一等并列卡
且连接即跳转,Owner 手测后否决——保留原页面格式、订阅默认收在折叠里、
不自动跳转),ProviderSettings 的 Sign-in tab 原位引用,逻辑单份。

## 两层状态,严禁混淆(继承自 ProviderSettings 的既有设计)

1. **OS 凭据态**(CLI 自有):`/claude-status`、`/codex-status` 探测;
   claude 的 Login/Re-login/Logout 仅 Tauri(web 模式给终端提示),codex
   永远只给终端提示(无 Tauri IPC)。登录 600s 倒计时自动 SIGTERM
   (`cancelClaudeLogin`)的机制原样搬入。
2. **Provider 记录态**(NarraNexus 自有):由 **props 下发**
   (`claudeCard` / `hasCodex`),写入走 **parent 的 `addProvider`**——
   parent 在成功后刷新自己的 provider 列表,记录态经 props 回流。本组件
   只拥有 CLI 状态生命周期。

## 无 onConnected(被删的死接口)

第一稿有 `onConnected` 回调(连接成功即通知父级跳转),Owner 否决自动
跳转后没有任何生产调用方传它,本地 review 判为 YAGNI 死接口并删除——
三个 handler 直接 `await addProvider(...)`;setup-token 路径仍依赖
addProvider 的返回值决定是否清输入框(失败保留输入供重试,有测试钉住)。
**不要**因为"以后可能要跳转"把它加回来:Owner 已明确否决该交互。

## 云端门禁(在本组件内,真实生效)

status 路由对 cloud 非 staff 返回 `allowed: false`(与 403 OAuth 卡型
同一个谓词);本组件读到任一 `allowed === false` 即 **return null**——
所有调用方(SetupPage 折叠区、Settings add modal)自动继承。第一稿把
门放在 SetupPage 的 mode 判断上、注释声称 "Settings 沿用 allowed 标志",
但前端当时**根本没读过**这个字段——review grep 证伪。判据必须
`=== false`:local 与 cloud-staff 下该字段是 undefined,truthiness 判断
会把本组件要修的 P0(本地订阅)一起关掉。SetupPage 仍保留
`mode !== 'cloud-web'` 外门(顺带隐藏区块标题;负向匹配让未 hydrate 的
null mode 向 local 开放)。后端 403 始终是真正的安全边界。

## 搬运保真说明

JSX(claude 卡 A/B/C 三段 + codex 卡)、`CLAUDE_LOGIN_TIMEOUT_SEC`、
`formatCountdown` / `formatExpiresAt` 均从 ProviderSettings 原样搬入,
setup-token 的"原位升级保槽位"注释与行为不变。两张卡有
`data-testid`(claude-connect-card / codex-connect-card)——按钮文案共用
同一 i18n key,测试必须 `within()` 限定。测试:
`__tests__/SubscriptionConnect.test.tsx`(claude add / codex add /
token 成功清输入 / 失败留输入 / allowed:false 渲染空 / local 双卡),
mock 的两个 status 路由返回**可区分**的 payload,防止 claude/codex
断言互相顶替。
