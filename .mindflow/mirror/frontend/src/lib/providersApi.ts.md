---
code_file: frontend/src/lib/providersApi.ts
last_verified: 2026-08-28
stub: false
---

# providersApi.ts — providers 族的共享类型/映射 + ProviderSettings 的 legacy 裸 fetch

## 现在的定位(review 第 3 轮重构后)

**新代码的传输层在 ApiClient**(api.addProvider / getClaudeStatus /
getCodexStatus / getProviders)——只有它带 session-death 守卫(401 +
confirmSessionDeath,2026-08-02 事故正是本资源)和 FastAPI detail 提取。
本模块保留:

- `ProviderRow` / `CliStatusPayload` — 共享类型(api.ts type-only 反向
  引用,无运行时环;`allowed` 仅 cloud 非 staff 为 false,判据必须
  `=== false`)。
- `providerErrorMessage(err, t)` — api.addProvider 失败(抛 ApiError)
  → 用户文案的单份映射:有 detail 原样透出、ApiError 无 detail(如网关
  502 HTML)→ failed、非 ApiError(fetch 拒绝)→ networkError。
- `authFetch` / `providerApiUrl` — **LEGACY,仅供 ProviderSettings 既有
  裸端点**(delete/test/models/sync;迁移是后续项)。新调用方禁止使用
  ——它没有 session 守卫,这正是 review 打回第一版 postProvider 的原因。

## 历史教训(保留)

- authFetch 委托 [[authHeaders]] 单一解析点;第一稿手抄解析被打回
  (2026-05-18 跨用户 bug 族)。header 用 `headers.set` 逐个写,
  `new Headers(getAuthHeaders())` 会丢 Content-Type。
- 第二稿的 `postProvider`(裸 fetch、不查 response.ok)被第 3 轮打回:
  session-dead 401 只会变成一行红字,502 HTML 被说成"网络错误"。
- 仓库仍有三处遗留手工解析(api.ts / arenaLanding.ts / artifactsApi.ts),
  别加第四处。
