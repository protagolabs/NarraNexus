---
code_file: src/narranexus/cli/main.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-03（批 2e）— `narranexus plugin …` / `narranexus docs gen`

所有变更动词走与工场 API 相同的内核对象（`Installer`/`RegistryStore`/`Bisect`），CLI 与 UI 永远不会对
状态各执一词。`doctor` 只读：不 boot，用 `discover`+`plan_load` 打印会加载/被阻/被拒；`publish-check` 是
发布清单（bronze 门）；`new` 用 `scaffold` 拼模板；`enable --ack` 顺便确认权限。异常一律变成 `error:` 行
+ 退出码，不吐 traceback。`[project.scripts]` 里注册为 `narranexus`。

## 2026-09-04 · builtin.teams as a feature-level plugin (batch 3c.2)

`plugin enable|disable <builtin.*>` toggles a builtin through registry.json `builtin_overrides` (protected builtins refuse, unknown ids error) instead of the user-plugin record path.

Batch 6c: `narranexus dist doctor [path] [--json]` (resolve against this engine, print plugin rows / excluded / size / problems, exit 1 on any problem) and `narranexus dist lock [path] [--out]` (write `narranexus-dist.lock.json`).

Batch 6c.3: `narranexus create-app <id> [--dir --display-name --base --auth --deployment]` and `narranexus build <path> --target desktop|docker|wheel [--out --dockerfile --dry-run]`.

Batch 6d: `plugin list` rows carry `quality` (read from each plugin's manifest).

2026-09-07: `narranexus slots [--domain --json --toml-template]`, `narranexus bind <slot> <provider…>`, `narranexus unbind <slot>`.
