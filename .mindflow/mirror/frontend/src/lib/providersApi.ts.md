---
code_file: frontend/src/lib/providersApi.ts
last_verified: 2026-08-28
stub: false
---

# providersApi.ts — /api/providers 的共享鉴权 fetch + URL builder

## 为什么存在

2026-08-28 P0(landing 只配订阅走不通)把 ProviderSettings 的 Sign-in
tab 抽成了 [[SubscriptionConnect]],SetupPage 也需要一个瘦 addProvider
包装——三个消费者(ProviderSettings / SubscriptionConnect / SetupPage)
不能各抄一份 `authFetch`,于是从 ProviderSettings 提到 lib。

## 语义(与 2026-05-18 修复一致,勿破坏)

- **身份只走 header**(X-User-Id 本地 / JWT Bearer 云端),**绝不进
  query string**——后端 2026-05-18 起删除了"users 表第一行"兜底,缺
  header 直接 401,这是正确的失败方式。
- `providerApiUrl` 每次调用现取 `getApiBaseUrl()`(不在 import 时捕获),
  local/cloud 切换后无需 re-mount 即指向正确 host。
- `authFetch` 直接读 localStorage 的 config 快照而非 zustand hook——
  纯 async handler 和非 React 调用方也能用;storage 损坏时退化为无鉴权
  请求,由后端 401。

## 注意

ProviderSettings 里的 `providerUrl` **仍保留为组件内 useCallback**
(依赖 userId 以在用户切换时强制 refreshConfig 重跑)——它构造的 URL
与 `providerApiUrl` 相同,但 re-render 语义是组件自己的事,别把它也
"顺手"换成本模块的裸函数。
