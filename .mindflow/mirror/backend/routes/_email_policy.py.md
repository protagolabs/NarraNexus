---
code_file: backend/routes/_email_policy.py
last_verified: 2026-10-28
stub: false
---

# _email_policy.py — 自助注册拒收的邮箱域名

## 为什么存在

`/api/auth/signup/send-code` 与 `/api/auth/signup` 天然无认证，send-code 还消耗
我们的 NetMind 发信通道。2026-10-28 事件里攻击者用一次性邮箱批量注册。本模块是
「哪些地址不让注册」的唯一答案，独立成私有模块，路由只 import `is_blocked_email`。

## 设计决策

- **小而显式的 frozenset**（~57 个知名一次性邮箱域），diff 可审；否决「vendored
  第三方大列表」——会在我们不知情时变化，且误伤真实邮箱服务商。
- **env 扩展** `NARRANEXUS_BLOCKED_EMAIL_DOMAINS`（逗号分隔，容忍空格/前导 `@`/
  首尾 `.`/大小写），**每次调用读取**（一次 split，成本可忽略），运维改 env 即生效。
- **按 DNS label 后缀匹配**：列出的域屏蔽自身与所有子域（`eu.mailinator.com`），
  不误伤 `notmailinator.com`，也不被 `mailinator.com.evil.org` 骗到。
- 无 `@` 的字符串返回 False——地址合法性是 `EmailStr` 的职责。

## 上下游

- **被谁用**：`backend/routes/auth.py` 的 `_refuse_blocked_email`，在 send-code 与
  signup 中**先于任何限流桶**执行（被拒请求不花 IP 桶、不分配 email key、不调上游）。
- 拒绝文案在路由侧，刻意中性（不出现 "disposable"，免得告诉探测者换哪个域）。

## Gotcha

- **触发**：在列表里加一个真实邮箱服务商的父域（如某个大 ccTLD 二级域）→
  **症状**：该服务商所有用户无法注册 → **根因**：后缀匹配会屏蔽全部子域。`cc.cd`
  就是这类「二级公共后缀」，加条目前确认它是一次性服务自己的域。

测试：`tests/backend/test_email_policy.py`、`tests/backend/test_signup_abuse_limits.py`。
