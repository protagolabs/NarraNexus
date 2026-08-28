---
code_file: frontend/src/lib/providersApi.ts
last_verified: 2026-08-28
stub: false
---

# providersApi.ts — /api/providers 的共享管道(URL、鉴权 fetch、POST 契约、行类型)

## 为什么存在

SubscriptionConnect 抽出后 /api/providers 有了三个前端调用方
(ProviderSettings / SubscriptionConnect / SetupPage)。这里收口的是:

- `authFetch` — **委托 [[authHeaders]] 的单一解析点**取身份 header。
  第一稿手抄了 localStorage 解析(还比原版弱:少了 string 守卫),
  被本地 review 打回:两份身份解析对同一份 storage 可能给出不同结论,
  正是 2026-05-18 跨用户写入事故那一族 bug 的起点。header 必须
  `headers.set` 逐个写——`new Headers(getAuthHeaders())` 会丢掉调用方的
  Content-Type。
- `postProvider` — POST /api/providers 的 body/错误契约单份。返回
  `{ok, detail}`,**detail 为 null 表示网络层失败、字符串(可空)是
  后端给的原因**——调用方据此选 networkError / failed 文案。**不带
  副作用**:两个调用方成功后的刷新编排不同(ProviderSettings 重拉
  自己的列表,SetupPage bump refreshToken),必须留在调用方。
- `ProviderRow` — 三个调用方共用的行类型(此前三处各写一份形状)。

## 注意

ProviderSettings 里的 `providerUrl` 仍是组件内 useCallback(依赖
userId,用户切换时强制 refreshConfig 重跑),但函数体已委托本模块的
`providerApiUrl`——re-render 语义在组件,URL 语义在这里,别合并。
