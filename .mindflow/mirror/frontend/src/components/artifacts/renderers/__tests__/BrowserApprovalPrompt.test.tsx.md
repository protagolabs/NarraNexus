---
code_file: frontend/src/components/artifacts/renderers/__tests__/BrowserApprovalPrompt.test.tsx
last_verified: 2026-09-23
stub: false
---

# BrowserApprovalPrompt regressions

普通访问已免授权，审批夹具改用独立下载能力；明确操作、失败重试和作用域测试保持覆盖。

Consent must name the whole origin and capability and require an explicit button
choice. Tests cover exact decision/lifetime arguments, no form-submit defaults,
pending controls, caught failures and retry. Context-sensitive turn/thread choices
must follow `allowed_lifetimes`; omitted metadata must never imply either context.
The turn grant is tested independently of the conversation grant.
