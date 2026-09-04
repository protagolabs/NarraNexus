---
code_file: frontend/packages/sdk/src/vitePreset.ts
last_verified: 2026-09-04
stub: false
---

## 2026-09-03（批 2e）— 插件 bundle 的构建预设（纯数据，不 import vite）

单文件 ESM、宿主库（react/react-dom/router/zustand/i18next/lucide）external 并映射到
`window.__narranexus_host__.<prop>`（`hostShimModule` 生成运行时垫片）；任何打包器都能消费，且可单元测试。
