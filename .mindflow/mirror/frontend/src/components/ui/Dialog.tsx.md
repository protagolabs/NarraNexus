---
code_file: frontend/src/components/ui/Dialog.tsx
last_verified: 2026-08-20
stub: false
---

## 2026-08-20 — 卡片背景加 `bg` override

新增可选 prop `bg`（默认 `'var(--nm-raised)'`，跟以前行为完全一样）——
[[../../pages/CreateAgentPage.tsx]] 想要弹窗外壳是纯白
(`var(--nm-card)`) 而不是默认的暖色 raised 调，卡片背景在组件内联
`style` 里设置，`className` 覆盖不了它，所以开了这个 prop 而不是让调用
方传 className 硬 hack。没传这个 prop 的调用方（AwarenessPanel /
SettingsModal / InstallDialog / AgentLlmConfigPanel 等）行为不变。

# Dialog.tsx — Custom portal modal with Escape key and scroll lock

## 为什么存在

Renders via `createPortal(document.body)` to escape any parent `transform` that would break `position: fixed` stacking. This is the concrete reason it's custom rather than using a shadcn Dialog.

## 上下游关系
- **被谁用**: `AwarenessPanel` (edit awareness), `SettingsModal`, `InstallDialog`, [[../../pages/CreateAgentPage.tsx]] (custom `bg`).
- **依赖谁**: `Button` (close button), `createPortal`.

## 设计决策

Handles `document.body.style.overflow = 'hidden'` on open to prevent background scroll. Cleans up on unmount. The nine size presets (`sm` through `6xl`) cover all use-cases without needing `className` overrides.

## Gotcha / 边界情况

- Does not use Radix `Dialog` primitive — no focus-trap or ARIA dialog role. If accessibility is required, this needs an upgrade.
- Sub-components `DialogContent` and `DialogFooter` are separate named exports — they are not `Dialog.Content` sub-components. Import them explicitly.
