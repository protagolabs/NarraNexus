---
code_file: frontend/src/pages/settings/registerBuiltinSections.ts
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — 在设置 chunk 内注册内置分区

`SettingsPage.tsx` 顶部 side-effect import。放在这里而不是 `platform/builtin.ts` 的原因：让分区组件留在
lazy 的设置 chunk 里并同步渲染，bundle 形状与页面行为与改造前一致。`if (!has('providers'))`
让重复 import（测试 setup 与页面）幂等。顺序值 10..80 对应原 `NAV_ITEMS` 顺序。
