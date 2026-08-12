---
code_file: frontend/src/components/auth/SignUpDialog.tsx
last_verified: 2026-08-12
stub: false
---

## 2026-08-12 — Mark 前端批：弹窗 Esc/背景关闭 + 改邮箱重置验证码

- **Esc/背景关闭（item 3）**：此前只有右上角 X 能关。加 `document` keydown 监听（Escape→onClose，effect 清理），并给外层遮罩 `onClick`（仅 `e.target === e.currentTarget` 即点到遮罩本身时关，点卡片内部不关）。
- **改邮箱重置验证码状态（item 7）**：email 的 onChange 改走 `onEmailChange`——一旦已发过码（`codeSent || cooldown>0 || code`）就清 `codeSent/cooldown/code`，避免 UI 暗示旧邮箱的验证码对新邮箱有效。
见 `SignUpDialog.test.tsx` 新增三条（Esc/背景/点内部不关）+ 改邮箱重置。

# SignUpDialog.tsx — 在我们自己的页面上注册

## 它替掉了什么

原来「Create account」是一个 `<a target="_blank">` 跳到 netmind.ai,把用户在流程
中途交出去;回来(如果回来了)只看到一个登录框,没有任何交代。用户反馈的就是
这个。

## 为什么不做成分步向导

三个字段而已。唯一的顺序约束是「验证码必须先请求才能填」,其余随便填 —— 一屏
全露出来,正是让这个表单显得短的原因。

## 它不负责登录

拿到「账号已创建」之后,把凭据交回登录页,由页面既有的 `netmind.emailLogin`
去建立会话。**建立会话的逻辑只有一处**;在这里再写一份就是第二个会漂移的地方。

## 验证码那个字段的坑

`FormField` 会把 label 的 `htmlFor` id 注入给它的**第一个子元素**。所以把
输入框和「发送」按钮一起包进一个 `<div>`,label 就指到了 div 上 —— 一个
什么都没标注的 label。按钮必须放在 FormField **外面**。

(这不是理论问题:测试里 `getByLabelText(/verification code/i)` 直接报
「element associated with this label is non-labellable」把它抓了出来。)

## 密钥

密码和验证码只存在组件 state 里、只传给 `api.*`。不进日志、不进 URL、不进埋点。
