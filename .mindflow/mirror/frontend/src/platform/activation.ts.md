---
code_file: frontend/src/platform/activation.ts
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（批 2d）— 前端激活事件

对应内核 `Activator`：每插件每次页面加载只激活一次；失败进 errorSink（kind=chunk）并记住，事件不重试，
`activateNow` 显式重试。`activationState` 返回**稳定快照**（状态没变就是同一对象）——`useSyncExternalStore`
每次拿新对象会无限重渲染（测试实锤 "Maximum update depth"）。
