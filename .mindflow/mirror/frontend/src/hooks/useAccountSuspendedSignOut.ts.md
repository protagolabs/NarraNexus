---
code_file: frontend/src/hooks/useAccountSuspendedSignOut.ts
last_verified: 2026-10-30
stub: false
---

# useAccountSuspendedSignOut.ts — 封号账号退出登录，停掉所有轮询

## 为什么存在

后端账号状态闸门对封号（suspended / banned）账号的**每个**请求回 403 `account_suspended`。前端原本不处理这个
码，开着的标签页会照常每 10/15/30 秒轮询，成串拿 403；边缘监控 v2 按 400/403/404/405 失败数计，每分钟 ≥20
次就封 IP 24 小时——被封的是整个出口 IP，同一办公室 NAT 后的其他人一起遭殃。前端失败守卫能把单个标签页压到
约 10 次/分钟，但多标签页或尚未刷新到新前端的旧页面仍可能越线。

## 做什么

[[api]] 遇到 403 `account_suspended` 派发 `narranexus:account-suspended`；本 hook 在已登录时 `logout()`
（轮询全部以 userId 为前提，随之停止；之后的请求不带凭据，回 401，边缘不计数），并返回
`[显示提示, 关闭]` 给 [[App]] 渲染「账号不可用，已退出登录」横幅。未登录时忽略。重新登录会被登录闸门拒绝，
所以退出是终态，不是会被立刻撤销的状态。
