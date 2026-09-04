---
code_file: frontend/src/platform/bootPlugins.ts
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（批 2d）— 应用级装配

`main.tsx` 在内置注册之后调用：先把 errorSink 的上报器指向 `POST /api/plugin-factory/{id}/errors`，再
`loadPlugins()`；不阻塞首帧。
