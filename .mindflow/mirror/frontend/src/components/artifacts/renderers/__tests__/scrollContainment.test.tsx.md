---
code_file: frontend/src/components/artifacts/renderers/__tests__/scrollContainment.test.tsx
last_verified: 2026-08-04
stub: false
---

# scrollContainment.test.tsx — 渲染器滚动归属契约测试

## 为什么存在

云端 artifact 滑不动（2026-07-13 用户报告，Base recvpm05jsLg3o）的根因
是 markdown/csv 渲染器根节点没有高度约束：高度=内容高度、自身
overflow-auto 永不溢出，真溢出发生在列容器并被 overflow-hidden 静默
裁掉。列容器的裁剪是拖拽冻结机制的依赖项，不能改——所以契约是
**渲染器自己拥有滚动**。本测试把这条契约钉死，防止将来某次样式改动
把 h-full 或 overflow-auto 摘掉、bug 悄悄回归。

## 断言内容

1. MarkdownRenderer 的 `.markdown-content` 容器：`h-full` +
   `overflow-auto` + `overscroll-contain`。
2. CsvRenderer 的 table wrapper（table 的父节点）：同上三项——纵向
   和宽表横向滚动都归它。
3. ArtifactRenderer 的统一包装盒：`h-full` + `w-full` 且**不含
   absolute**——该组件与 ArtifactZoomModal 共用，absolute 会逃逸
   弹窗的 scale-sizer 层、破坏 zoom 平移。

## Mock 模式

沿用 HtmlRenderer.test.tsx 的做法：mock `@/services/artifactsApi`
（getRawUrl / fetchArtifactText / fetchArtifactBlobUrl）+ `@/lib/tauri`
（isTauri=false）。fetchArtifactText 按 URL 是否含 csv 返回表格或
markdown 文本。
