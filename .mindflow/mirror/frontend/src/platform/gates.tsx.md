---
code_file: frontend/src/platform/gates.tsx
last_verified: 2026-09-04
stub: false
---

## 2026-09-03（批 2d）— 页面/面板 gate

加载器替插件在 `PAGES`/`PANELS` 里登记的占位组件：挂载时 `fireActivation(onPage:/onPanel:)`，激活完成后渲染
插件在 `activate(host)` 里以同 id 替换登记的真组件；失败显示错误而不是空白。

## 2026-09-04 · UI slot points (batch 3d.2)

`makeSlotGate` (renders nothing, activates on mount, then the real component), `makeRendererGate` (matches the declared shape, activates on first render, defers to the real renderer — yields null so the shell bubble is not duplicated) and `makeTimelineGate` join the page/panel gates; the status element moved to PluginStatus.tsx.
