---
code_file: frontend/src/platform/builtin.ts
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — 壳自己的贡献（页面、侧栏、面板）

「内置即插件」（D4）：壳通过与插件相同的注册表贡献自己的页面/侧栏行/面板，集中在一个文件、按 UI 顺序。
`main.tsx` 在首帧前 import 一次。页面与面板组件保持 lazy，本文件只往首包加元数据。
设置分区**不在这里**注册：它们由 `pages/settings/registerBuiltinSections.ts` 在设置 chunk 内注册，
以保持面板与页面同 chunk、同步渲染（否则 SettingsPage 测试与 bundle 形状都会变）。
