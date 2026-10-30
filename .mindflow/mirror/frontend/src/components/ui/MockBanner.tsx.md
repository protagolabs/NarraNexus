---
code_file: frontend/src/components/ui/MockBanner.tsx
last_verified: 2026-10-30
stub: false
---

# MockBanner.tsx — mock 模式角标

mock 模式下右下角的小标签（「Mock data」+ turn off），提醒当前看到的是 fixtures
而不是后端数据；非 mock 模式渲染 null。开关读 [[flag]]（`lib/mock/flag`），
**不** import mock 实现——它在 App 里常驻，静态引用实现会把 fixtures 拉进生产
首屏（2026-10-30 拆分，见 [[flag]]）。
