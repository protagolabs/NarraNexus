---
code_file: src/narranexus/cli/main.py
last_verified: 2026-09-04
stub: false
---

## 2026-09-03（批 2e）— `narranexus plugin …` / `narranexus docs gen`

所有变更动词走与工场 API 相同的内核对象（`Installer`/`RegistryStore`/`Bisect`），CLI 与 UI 永远不会对
状态各执一词。`doctor` 只读：不 boot，用 `discover`+`plan_load` 打印会加载/被阻/被拒；`publish-check` 是
发布清单（bronze 门）；`new` 用 `scaffold` 拼模板；`enable --ack` 顺便确认权限。异常一律变成 `error:` 行
+ 退出码，不吐 traceback。`[project.scripts]` 里注册为 `narranexus`。

## 2026-09-04 · builtin.teams as a feature-level plugin (batch 3c.2)

`plugin enable|disable <builtin.*>` toggles a builtin through registry.json `builtin_overrides` (protected builtins refuse, unknown ids error) instead of the user-plugin record path.
