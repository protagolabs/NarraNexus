---
code_file: src/narranexus/kernel/plugins/manifest.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（预审修订）— `api` 版本严格相等；`declares` 限本插件命名空间；`hosts` 空=全部

`api[kind]` 必须**等于**宿主版本（bump 即破坏，policy §5「不匹配 fail-closed」）。`declares` 的路径
必须在 `<plugin_id>.` 之下（可归因，且祖先可安全自动补为该插件的命名空间）。`SlotDeclaration`
加 `stability`。`hosts` 空元组的语义「全部宿主」写在字段注释并由 `effective_hosts()` 承担。
`ui.pages` 的激活推导改为精确匹配（不再误配 `ui.pagesomething`）。

## 2026-09-03 — `narranexus-plugin.json` 的模型：声明式、严格、扩展位感知

启动期只读 manifest 不 import 代码（spec §7.4 / §10 性能预算），所以 UI 元数据、装载计划、
拒绝理由全部要从 manifest 得到。pydantic v2 `extra="forbid"` + frozen：未知键是错误（防
拼错静默无效），对象不可变（可放进 loader 的快照）。字段名与 spec §5.1 一致，JSON 侧驼峰
（`displayName/minAppVersion/afterDependencies/activationEvents/distributionOnly`），Python 侧蛇形
（`populate_by_name`）。
`provides` 的 key 是扩展位路径（D25 后取代 `contributes`），校验分两层：模型层只查形状
（路径语法、`module.path:Symbol`），`parse_manifest` 再对着 `SlotTree` 查存在性、元数匹配
（one 给单符号 / many 给列表）、`distribution_only` 位必须由 `distributionOnly` 的 manifest 提供。
本插件自己 `declares` 的位允许出现在自己的 `provides` 里（声明并提供默认实现）。
`redeclares` 必须是本插件所提供复合位的后代且在树里已知（§6.3 规则 3）。`api` 的 kind 必须在
`API_VERSIONS` 且不高于宿主；`minAppVersion` 与宿主版本比较用 `compat.Version`。
`derive_activation_events`：VS Code 1.74 规则的批 0 子集——`ui.pages`/`ui.panels` 推出
`onPage/onPanel:<id>`，其余提供项推出 `onStartup`。`builtin.` 前缀保留，只有
`allow_builtin=True`（内核 `builtins.py`）能用。
