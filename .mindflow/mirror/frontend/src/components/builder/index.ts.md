---
code_file: frontend/src/components/builder/index.ts
last_verified: 2026-09-03
stub: false
---

# index.ts — builder 目录的 barrel

只导出组件与纯映射。**`isConfigDraft` 不在这里** —— 它在
[[builderPrompt.ts]]，和它测试的那个标题约定放在一起；组件文件导出非组件会
触发 eslint 的 `react-refresh/only-export-components`。

目录职责见 [[_overview]]。
