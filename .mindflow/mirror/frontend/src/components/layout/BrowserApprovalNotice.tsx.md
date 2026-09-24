---
code_file: frontend/src/components/layout/BrowserApprovalNotice.tsx
last_verified: 2026-09-23
stub: false
---

## 2026-09-23 - 持久化登录交接

普通网址访问完全免授权，后端不再返回历史 access 请求；此宿主仅消费独立能力审批与登录通知。

现有 approvals 轮询同时返回 `kind: login` 的请求。按类型分流至 BrowserLoginNotice，
不把登录当站点授权、不调用 resolveBrowserApproval。沿用 agent 隔离和插件注册门控；
登录提示只在服务端真实交还控制并从待处理列表移除后消失。

## 2026-09-23 - Follow browser feature registration

The shell subscribes to PANELS and mounts the polling child only while the
Browser panel is registered. Its owner is `builtin.browser`, so plugin disable
unmounts any visible approvals and cleans up pending polls. A disabled backend
feature must not leave an app-wide error banner or continue approval requests.

## 2026-09-22 - Request ownership and recovery

The shell owns approvals for the active chat agent. A keyed child gives each
agent an independent request lifecycle, so switching agents immediately hides
the previous prompt and late successes or errors cannot modify the new view.
Polling schedules its next request after settlement, with cleanup invalidating
late responses. Confirmed decisions are removed immediately and remembered for
this mounted agent, preventing an older poll from restoring an answered question.

Only an explicit `ok: true` resolves a question. A rejected HTTP call or
`ok: false` remains retryable through BrowserApprovalPrompt. Poll failures clear
stale questions, announce the fault, and offer an NM retry control while automatic
polling continues. The panel itself must not add another approval poller.

# BrowserApprovalNotice.tsx — 把待审批的站点送到用户真正在的地方

## 为什么存在

**因为同一个错误犯了三次。** 浏览器功能里的每一次「拒绝」都点名了一个目的地，
而每一次那个目的地都活在比消息传播范围更窄的地方：

1. 安装卡只在 `UrlRenderer` 的 stream 分支里 → 设置里没有入口
2. 补进了 `SettingsModal`（弹窗）→ 用户打开的是 `/app/settings`（页面），还是没有
3. 授权弹窗只在浏览器面板里 → 用户在**聊天**里读到拒绝，屏幕上根本没有面板

所以这个组件**不是面板组件**。它挂在 app shell 上，跟着用户正在对话的那个 agent，
出现在所有东西之上——一个 agent 正卡在上面的问题，优先级高于屏幕上别的任何东西，
否则那个 agent 看起来就是挂了。

## 设计决定

**轮询而不是推送。** 审批是 agent 的那一轮抬起来的，不在这个 tab 的任何 socket 上；
漏掉一个提示就是死锁——agent 在等一个从没被问过的人。

**面板不再自己轮询。** 曾经两边都轮询，于是同一个问题在两个地方各弹一次，
可能得到两个互相矛盾的答案。现在只有这里拥有它。

**回答后立刻从列表移除**，不等下一次轮询：把已经回答过的问题留在屏幕上，
是在邀请第二个（可能相反的）答案。

**轮询失败时清空而不是保留。** 后端重启时留着一个陈旧的提示，会让用户去回答一个
已经不存在的问题。

## 上下游

- 上游：`MainLayout`（app shell）
- 下游：[[BrowserApprovalPrompt]]、`api.getBrowserApprovals` / `resolveBrowserApproval`
- 设计：`reference/self_notebook/specs/2026-09-21-in-app-browser-design.md` §6
