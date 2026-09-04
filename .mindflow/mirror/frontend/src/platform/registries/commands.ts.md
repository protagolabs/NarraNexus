---
code_file: frontend/src/platform/registries/commands.ts
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（批 2d）— 命令注册表

⌘K 面板在壳自身命令之后列出注册表条目（label 可为 i18n key，`when` 谓词渲染时求值）。加载器为 manifest
声明的命令登记 gate（先激活再执行插件替换后的命令）。
