---
code_file: frontend/src/platform/pageRoutes.tsx
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — 路由表构造器：`PAGES` 条目 → `<Route>` 元素

从 `App.tsx` 抽出，唯一目的是可测：预审指出「App.tsx 消费注册表」只有静态比对、没有渲染证据。
`pageRouteElements(entries, wrappers)` 返回顶层路由（按 `guard` 套注入的 ProtectedRoute/PublicRoute）
与 `/app` 子路由两组；守卫组件由 `App.tsx` 注入，因为它们绑着壳的登录状态。
`/app` 下的页面必须声明 `protected`（父路由已守卫，声明 `public` 是插件的错觉，直接抛错）。
`__tests__/pageRoutes.test.tsx` 在 MemoryRouter 里渲染：守卫包裹、`open` 无包裹、首帧后注册的页面
不重挂载即出现（`useRegistryEntries` 订阅）、`null` element 占位、`/app` 下 public 被拒。
