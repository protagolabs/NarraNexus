---
code_file: src/narranexus/kernel/plugins/loader.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（预审修订）— `load_order`、`declares` 先于 `provides`、空贡献可见

`load_order(manifests)`：内置按声明序、用户插件按 id 排序——这才是 `Registry.names()` 跨重启字节
稳定的真正保证（此前只在 docstring 里承诺）。插件的 `declares` 在解析 `provides` 前先进 slot 树
（`create_namespaces=True` 自动补出 `<plugin_id>` 命名空间祖先），所以「声明并提供自己的扩展点」
一次装载即成立。`hosts` 为空按 `effective_hosts()`（= 全部宿主）解释；某个 provides 符号解析出
零个贡献时记 debug 日志（`system.py` 在本地合法为空，但要可 grep）。依赖拓扑排序留批 2。

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
