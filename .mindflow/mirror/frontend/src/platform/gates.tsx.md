---
code_file: frontend/src/platform/gates.tsx
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（批 2d）— 页面/面板 gate

加载器替插件在 `PAGES`/`PANELS` 里登记的占位组件：挂载时 `fireActivation(onPage:/onPanel:)`，激活完成后渲染
插件在 `activate(host)` 里以同 id 替换登记的真组件；失败显示错误而不是空白。
