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
  后端给的原因**。**不带副作用**:两个调用方成功后的刷新编排不同
  (ProviderSettings 重拉自己的列表,SetupPage probe + bump
  refreshToken),必须留在调用方。
- `providerErrorMessage(detail, t)` — 失败→用户文案的**单份映射**
  (review 第 2 轮:两个调用方曾逐字重复这段映射,而 docstring 还谎称
  "措辞不同")。后端非空 detail 原样透出,两个兜底才走 i18n key。
- `ProviderRow` — 三个调用方共用的行类型(此前三处各写一份形状)。

**"单一解析点"的边界**:authFetch 委托的 [[authHeaders]] 是 canonical
解析点,但仓库仍有三处遗留手工解析(api.ts / arenaLanding.ts /
artifactsApi.ts)——别再加第四处;收敛它们超出本 PR 范围。

## 注意

ProviderSettings 里的 `providerUrl` 仍是组件内 useCallback(依赖
userId,用户切换时强制 refreshConfig 重跑),但函数体已委托本模块的
`providerApiUrl`——re-render 语义在组件,URL 语义在这里,别合并。
