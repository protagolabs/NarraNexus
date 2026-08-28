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

## onConnected 契约

**只在订阅卡成功 add/upgrade 后触发**(Add as Provider / setup-token /
codex 三条路径共用 `connect()` 收口),可选。SetupPage **不传**它——
Owner 决策:订阅连接不自动跳转,页脚实时翻 "Get Started" 由用户自己走。
addProvider 返回 false 时**不得**触发。

## 云端边界

调用方负责不在云端渲染:SetupPage 以 `mode === 'local'` 门;Settings 的
add modal 沿用 status 路由的 `allowed` 标志(后端 cloud+非staff 403 是
真正的安全边界,前端只是不做无效广告)。

## 搬运保真说明

JSX(claude 卡 A/B/C 三段 + codex 卡)、`CLAUDE_LOGIN_TIMEOUT_SEC`、
`formatCountdown` / `formatExpiresAt` 均从 ProviderSettings 原样搬入,
setup-token 的"原位升级保槽位"注释与行为不变。测试:
`__tests__/SubscriptionConnect.test.tsx`(add/token/codex 三路 +
失败不触发 onConnected)。
