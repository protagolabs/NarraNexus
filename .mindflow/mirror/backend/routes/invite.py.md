---
code_file: backend/routes/invite.py
last_verified: 2026-05-14
stub: false
---

# invite.py — 公开邀请码申请端点

## 为什么存在

云端注册门禁的"前门"。访客在 website 填邮箱 → 这个端点生成唯一邀请码、
发邮件、按 Mode B 决定立即发码还是进 waitlist。是邀请码机制唯一的**公开**
surface（admin 操作在 `/api/admin/invite`）。

## 上下游关系

- **被谁用**：`narranexus-website` 的 `app/api/invite/route.ts` 代理转发；
  `backend/main.py` 以 `/api/invite` 前缀挂载。
- **依赖谁**：`InviteCodeRepository`（幂等判断 + cap + create）、
  `utils.mailer.send_email`、`backend.config.settings.invite_auto_issue_cap`、
  `backend.auth._is_cloud_mode`、`_rate_limiter.SlidingWindowRateLimiter`。

## 设计决策

- **JWT 豁免**：申请者还没账号，所以 `/api/invite/request` 必须在
  `backend/auth.py::AUTH_EXEMPT_PATHS` 里。admin 路由走 `/api/admin/invite`
  另一前缀，仍需 staff JWT。
- **响应永不回显 code**：`InviteRequestResponse` 没有 `code` 字段。code 只走
  邮件——否则限流可被"读响应"绕过。
- **幂等**：同邮箱已 `used` → 提示去登录；已 `issued` → **重发同一个码**，
  绝不二次生成；已 `waitlisted` → 告知在排队。一个邮箱永远只有一个有效码。
- **Mode B cap**：新申请时 `count_active()`（issued+used）≥ cap → 建
  `waitlisted` 行（不发邮件）；否则建 `issued` 行并发邮件。
- **双限流**：per-IP 5/10min + per-email 3/hour，process-local。更强的反滥用
  靠 website 代理层的 Cloudflare Turnstile。
- **失败不 500**：整个 DB 段包在 try/except 里，异常返回 `success=False` +
  友好 error，不抛 500。邮件发送失败也不阻断——code 已落库，`email_sent=0`
  在 admin 列表里可见、可重发。

## Gotcha

- `_ip_limiter` / `_email_limiter` 是模块级 process-local 状态；多 worker
  部署下每个进程各算各的（可接受，单进程为主）。
- `_client_ip` 信任 `X-Forwarded-For` 首跳——仅在确有反向代理时才准确。
- local 模式直接返回 error（邀请码只对 cloud 生效），对齐 `register()`。
