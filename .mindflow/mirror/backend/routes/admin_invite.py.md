---
code_file: backend/routes/admin_invite.py
last_verified: 2026-05-14
stub: false
---

# admin_invite.py — Staff 专用邀请码管理

## 为什么存在

Mode B 的运营侧：当自动发码到达 cap，新申请进 waitlist；管理员需要能看
全量码、把 waitlist 的码 promote 成 issued（并补发邮件）、撤销码。

## 上下游关系

- **被谁用**：`backend/main.py` 挂载（自带 `/api/admin/invite` 前缀）；
  云端运营/staff 调用。
- **依赖谁**：`InviteCodeRepository`（list_all / promote / revoke /
  get_by_code / mark_email_sent / count_active）、
  `backend.routes.invite._send_code_email`（promote 时补发邮件，复用同一
  封邮件正文，避免重复——铁律 #8）、`backend.config.settings`。

## 设计决策

- **鉴权 = staff JWT**，`_require_staff_or_raise` 与 `admin_quota.py` 完全
  一致：local 模式 503、无 user 401、非 staff 403。
- **前缀 `/api/admin/invite`**：落在 `auth.py::QUOTA_BYPASS_PREFIXES` 的
  `/api/admin` 下——JWT 照验，但跳过 quota resolver（运营操作不花 quota）。
- **promote 即补发邮件**：waitlisted 行当初没发邮件，promote 成 issued 时
  才发，并回写 `email_sent`。
- **revoke 不碰 used 码**：账号已建，撤码无意义；`repo.revoke` 只对
  issued/waitlisted 生效，否则返回 404。

## Gotcha

- 从 `invite.py` import 了私有的 `_send_code_email`——刻意复用邮件正文，
  不是手滑。若将来邮件正文要改，改 `invite.py` 一处即可。
