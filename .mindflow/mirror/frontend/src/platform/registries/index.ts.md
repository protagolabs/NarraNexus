---
code_file: frontend/src/platform/registries/index.ts
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（批 2d）— 导出 `THEMES`/`COMMANDS` 及主题辅助

## 2026-09-03 — 注册表包的公开入口

插件 bundle 只被允许 import 这一个文件（`.dependency-cruiser.cjs`
`plugins-only-import-contracts` 规则的例外项）。`registries-are-pure` 规则禁止本目录 import
components/pages/stores/lib/hooks：注册表只存类型与表，不认识任何具体页面。
