---
code_file: frontend/src/platform/registries/themes.ts
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（批 2d）— 主题注册表

主题 = token→值 的数据；注册时逐 key 对照生成的 token 表、值做安全字符校验（禁 `url(`），主题造不出组件不读的
变量、塞不进任意 CSS。`applyTheme` 只在根元素设这些变量并记录，`clearTheme` 精确移除。
