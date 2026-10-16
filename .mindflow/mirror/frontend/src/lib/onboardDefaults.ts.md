---
code_file: frontend/src/lib/onboardDefaults.ts
last_verified: 2026-10-16
stub: false
---

# onboardDefaults.ts — 把服务端的 per-source 默认模型表灌进前端

## 为什么存在
同一个"默认模型"决定原先在前端手抄一份(PR#413 review I2:改一个默认要同步 5 处,且前端看不到 `netmind_free` 的 env 覆盖)。
本文件 `refreshOnboardDefaults()` 调 `api.getProviderCatalog()`,把 `onboard_defaults` 交给 `agentFramework.applyOnboardDefaults`。

## 不做什么
不持有任何模型 id;不缓存/节流(调用方是两个设置面板的 `load()`,频率低);不 reject——请求失败(网络)保持原表,选择回落"列表首个"。前后端同发布,不为"老服务端"或测试 mock 弯曲生产代码(响应字段必填)。

## Gotcha
- **触发**:新增调用点不 `await` 它就渲染推荐提示 → **症状**:首屏提示用回落值 → **根因**:表是模块级可变状态,无响应式。两个现有调用点把它放进 `load()` 的 `Promise.all`。
