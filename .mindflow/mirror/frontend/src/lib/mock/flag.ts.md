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

- 除 `import type` / `export type` 外，任何源码静态引用 `lib/mock`（index /
  fixtures，含 `export … from`、副作用 import、`.ts` 后缀、尾斜杠等写法）都会把
  fixtures 拉回入口 chunk。两道闸：`__tests__/mockLazy.test.ts` 用 TypeScript 解析器取出每条
  import / export 声明的模块说明符，**解析成绝对路径**再比对（不靠正则枚举写法，正则
  曾在无分号文件里吞掉下一条语句；只有整条 `import type` / `export type` 豁免，
  `import { type A }` 在 verbatimModuleSyntax 下仍会产生 import，照样拦；本文件也在扫描范围内，唯一豁免是
  index.ts → ./fixtures）；构建后 `scripts/check_bundle_budget.mjs` 检查首屏文件不得
  含 `budget.json` 的 `initial_forbidden_strings`（fixtures 的用户 id `demo.user`），
  测试另钉住该标记串仍存在于 fixtures 里，改名不会悄悄让闸失效。
