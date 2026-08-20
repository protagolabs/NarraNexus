---
code_file: frontend/src/components/artifacts/kindRegistry.ts
last_verified: 2026-08-19
stub: false
---

# kindRegistry.ts — kind 能力注册表(单一事实源)

## 为什么存在

此前 kind 知识散落四处:ArtifactRenderer 的渲染表、ArtifactPreviewCard
的 `kind ===` 链、ArtifactDownloadMenu 的 ext 映射+isChart、
ArtifactsSection 的 label 映射。加一个 kind(或一种能力,如编辑)要猎遍
全部。现在每个消费者查一个描述符;新 kind = 一行描述符 + 一个 renderer
文件。

## 字段与消费方

| 字段 | 消费方 | 状态 |
|---|---|---|
| renderer | ArtifactRenderer | 活跃 |
| preview / previewPlaceholderKey | ArtifactPreviewCard | 活跃 |
| downloadExt / chartImageExport | ArtifactDownloadMenu | 活跃 |
| label | ArtifactsSection(缺省回退原始 kind 串) | 活跃 |
| editSurface / saveMode | 编辑外壳(C2~C4 逐步消费) | 声明先行 |
| selectionToAI | 选区→AI 通道(v1.5) | 声明先行 |

**声明先行是有意的**:office 编辑 spec 与 v1.5 不必再动表结构。

## editSurface 语义(无模式框架,2026-08-19 设计定稿)

**全系统没有 View/Edit toggle**——渲染面本身在 kind 允许处落光标:
block-editor(md,渲染即编辑器)/ per-element(html,点进元素失焦提交)/
resident-editor(csv,源即渲染常驻可编辑)/ office-watch(officecli
命令翻译)/ none(只读,改动走 AI)。saveMode 恰在 editSurface=none 时
为 null——这条不变量被 [[__tests__/kindRegistry.test.ts]] 钉死。

## 坑

- `ArtifactKind` 联合里没有 text/plain 或代码类 kind——常驻编辑器目前
  只有 csv 一员;将来加 code kind 就是注册表加行。
- lazy import 全部迁到本文件(模块级、单处),React.lazy 按 import 调用
  点 memoise——列与 zoom 弹窗仍共享 chunk,勿在别处再写同名 lazy。
- label 故意可缺省:office-live/x-url 沿用原始 kind 串显示(行为保持
  的重构约束);要给它们人话名先确认设置页文案预期。
