---
code_file: frontend/src/components/artifacts/renderers/ResidentTextEditor.tsx
last_verified: 2026-08-19
stub: false
---

# ResidentTextEditor.tsx — 常驻编辑面(editSurface=resident-editor)

无模式框架:源文本即渲染,常驻可编辑,没有 View/Edit 切换。状态全在
[[useArtifactEditor.ts]];本组件只有 chrome:CodeMirror(@uiw/
react-codemirror 懒 chunk)+ 保存条(显式保存,Cmd/Ctrl+S)+ 409 冲突
横幅二选 + 草稿恢复横幅 + 把 dirty 镜像进 artifactStore(tab 圆点)。
csv 用它;未来 code/纯文本 kind 直接挂同一组件(kindRegistry 加行)。
onLoadError 冒泡给宿主跑 heal(410→attempt)。
