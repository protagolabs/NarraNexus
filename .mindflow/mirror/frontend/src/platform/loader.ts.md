---
code_file: frontend/src/platform/loader.ts
last_verified: 2026-09-04
stub: false
---

## 2026-09-03（批 2d）— 插件加载器：元数据先行，代码按激活

`loadPlugins()`：GET `/api/plugin-factory` → 只取 enabled+loaded+有 frontend 的行 → `registerDeclaredUi` 按
manifest `frontend.ui` 给每个 page/panel/command 在注册表里放一个 **gate**（owner=插件 id）→
`registerActivation` → `fireActivation('onStartup')`。`activatePlugin`：桌面走 `plugin://<id>/…`、web 走工场
assets 路由；manifest 有 `integrity` 就先 sha256（`digestImpl` 可注入——jsdom 的 SubtleCrypto 跨 realm 拒收
ArrayBuffer，测试用 node:crypto）比对，再由 Blob URL `import()`（动态 import 没有 integrity 属性，所以自己校验后
再导入），Blob URL 登记到 errorSink 做归因；`plugin.activate(host)`。任何失败只上报不外抛。

## 2026-09-04 · builtin.teams as a feature-level plugin (batch 3c.2)

`loadPlugins` reads `data.builtins` from the factory listing and calls `disableBuiltinUi(id)` for every disabled non-protected builtin, which removes that owner from every shell registry — so a disabled `builtin.teams` has no `/teams/*` pages without any teams-specific code in the loader.
