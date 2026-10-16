---
code_file: frontend/src/lib/__tests__/onboardDefaults.test.ts
last_verified: 2026-10-16
stub: false
---

# onboardDefaults.test.ts — 服务端默认表 → 前端推荐表

mock `api.getProviderCatalog`:响应填充表(netmind agent/helper 选 V4.1-Flash 而非列表首个 omni)、请求失败不抛且回落列表首个。使用该 api 的组件测试在各自 mock 里补了 `getProviderCatalog`。
