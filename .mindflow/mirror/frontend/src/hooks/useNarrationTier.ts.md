---
code_file: frontend/src/hooks/useNarrationTier.ts
last_verified: 2026-08-30
stub: false
---

# useNarrationTier — 独白该不该按「进度」档渲染

## 为什么存在

[[uiStore]] 的 `interimNarration` 偏好有**三个**渲染面要读：
[[TurnTimeline]]（顶层渲染器）、[[ProcessPanel]] 与 [[TeamMemberPanel]]
（这两个再往 [[processShared]] 的 `ProcessEventRows` 传 prop）。

三份 `useUIStore((s) => s.interimNarration)` 就是三个「改缺省值 / 换来源时
会漏掉一个」的点。review 第 3 轮点名了这件事，收成一个薄 hook——
`hooks/` 本来就是这类薄封装的家（`useReducedMotion` 同规格）。

## 为什么 `ProcessEventRows` 不调它

它是**被两个面板复用的共享行渲染件**。共享渲染件里埋一个不在 props 上的
全局输入，下一个复用它的面板会遇到「events 传对了、显示还是不对」。
所以它收 `showNarration: boolean`（**无默认值**，漏传由 tsc 抓），
偏好由调用方用本 hook 解析。

[[TurnTimeline]] 自己调本 hook 是对的——它是顶层渲染器不是共享行组件；
改成从 props 传会让它的两个调用点（`MessageBubble` / `TeamMemberPanel`）
都被迫加一个与自己无关的 prop。
