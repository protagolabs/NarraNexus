---
code_file: src/narranexus/kernel/plugins/loader.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — 批 0 的 loader：只装内置，云端 fail-closed，import 只发生在这里

`discover(cloud, user_registry_path)` 现在只返回内置 manifest；`cloud=True` 时用户注册表路径
**按构造忽略**（D1 不是配置项，是代码形状），批 2 接 `registry.json` 时这个签名不变。
`load(registries, manifests, role)`：按 `hosts` 过滤角色 → 逐个 `provides` 解析 `module:attr` →
值必须是 `Contribution` 或其可迭代 → `registry_for(path).register_contribution(owner=插件 id)`。
内置失败 raise（内置坏了必须炸，与今天一致）；用户插件失败隔离进 `LoadReport.errors`（批 2 接
crash 计数与自动禁用）。每插件计时进 `PluginLoad.duration_ms`，供 §10 启动预算门使用。
与 import 期注册共存的机制：内置模块在 import 时用同一个 `Contribution` 对象注册，`Registry`
对同名同工厂对象的重复注册是 no-op，所以「先 import 再 load」和「只 load 不 import」得到同一张表
（`test_loading_twice_into_the_process_registries_is_idempotent`）。
