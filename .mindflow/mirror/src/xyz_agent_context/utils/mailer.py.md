---
code_file: src/xyz_agent_context/utils/mailer.py
last_verified: 2026-05-14
stub: false
---

# mailer.py — 通用事务邮件发送器（SMTP）

## 为什么存在

邀请码机制需要给用户发邮件。项目此前没有任何邮件基础设施。这里做一个
**provider 无关**的薄抽象：传输层用标准库 `smtplib` + `asyncio.to_thread`，
换供应商（个人 Gmail → AWS SES / Resend SMTP）只改环境变量、不改代码。
对应铁律 #9（不强依赖某一个外部服务）。

## 上下游关系

- **被谁用**：`backend/routes/invite.py`（发邀请码邮件）、
  `backend/routes/admin_invite.py`（promote waitlist 时补发）。
- **依赖谁**：仅标准库 `smtplib` / `email`，运行时读 `SMTP_*` 环境变量。

## 设计决策

- **通用 vs 场景特定分离**（铁律 #4）：本模块只会"发一封信"，不知道"信里写
  什么"。邀请码邮件正文由调用方拼。
- **未配置即 no-op**：`SMTP_HOST` 没设时 `send_email` 不抛错，只 log 并返回
  `False`。对齐 quota 子系统"功能关闭=无害空操作"的 pattern——local 模式 /
  没配 SMTP 的环境不会因此崩。
- **失败不阻断**：`send_email` 吞掉所有异常返回 `False`。邀请码即使发信失败
  也已生成并落库，admin 列表通过 `email_sent=0` 暴露、可手动重发。

## Gotcha

- `_send_sync` 是阻塞调用，必须经 `asyncio.to_thread` 跑，绝不能直接在事件
  循环里调。
- `SMTP_USE_TLS` 默认 true（STARTTLS）；Gmail / SES / Resend SMTP 都走 587 +
  STARTTLS。改成 465 隐式 TLS 需要换 `smtplib.SMTP_SSL`——v1 没做。
