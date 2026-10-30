---
code_file: frontend/src/lib/mock/flag.ts
last_verified: 2026-10-30
stub: false
---

# mock/flag.ts — mock 模式开关（唯一允许进首屏的 mock 代码）

## 为什么存在

mock 实现（[[index]] + fixtures，约 27KB）以前被 [[api]] 静态 import，每次生产
页面加载都背着它、只靠运行时 `?mock=1` 决定用不用。开关单独成文件后，首屏只带
这几十行：`MOCK_ENABLED`（读 `?mock=0|1` 并持久化到 localStorage）和
`setMockEnabled`（写 localStorage + reload）。[[api]] 用它决定是否建 Proxy，
[[wsManager]] 用它决定是否走 `runMocked`，[[MockBanner]] 用它显示角标；
[[main]] 在它为真时才动态 import 实现并装进 api。

## 坑

- 除 `import type` 外，任何源码静态 import `lib/mock`（index/fixtures）都会把
  fixtures 拉回入口 chunk；`__tests__/mockLazy.test.ts` 扫源码兜住。
