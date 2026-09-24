---
code_file: frontend/src/components/layout/__tests__/BrowserApprovalNotice.test.tsx
last_verified: 2026-09-23
stub: false
---

# BrowserApprovalNotice regressions

能力通知夹具使用 downloads，访问审批已移除；登录通知和跨 agent 请求隔离继续覆盖。

The shell must surface approvals without an open browser panel. Deferred requests
and fake timers pin ownership across agent changes, immediate clearing, serialized
polls, cleanup and suppression of already answered IDs from stale snapshots.
An `ok: false` decision must remain actionable, and a failed poll must provide an
announced fault and an effective retry. These tests mock the API and active agent
store so no real approval is created or resolved.

Plugin lifecycle regressions call the real `disableBuiltinUi('builtin.browser')`
path. They assert no requests for a disabled feature and cleanup of an already
visible prompt and its scheduled polls. Registry ownership is restored after each
test to keep feature state isolated.

登录交接测试使用真实路由和 UI store，验证从设置页打开聊天中的浏览器且使用
open 语义，不消费站点授权。接管期间提示保持，只有服务端待处理列表移除才消失；
界面不把交还控制称为认证成功。
