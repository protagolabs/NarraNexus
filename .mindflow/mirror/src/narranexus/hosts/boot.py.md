---
code_file: src/narranexus/hosts/boot.py
last_verified: 2026-09-04
stub: false
---

## 2026-09-04（批 3c.1）— 禁用的内置：在冻结前 `registries.remove_owner`

import 时登记的贡献（模块类等）在 stage1 之前按 owner 移除并 block 其钩子，`BootReport.disabled_builtins` 记录。

## 2026-09-03（批 2b.5）— 分阶段启动（spec §9.2）

一个进程角色一次：stage1 内置（`load()` 内部失败即 raise——内置坏了必须炸）；stage2 用户插件
（`discover` 读 `registry.json`，云端/安全模式只返回内置；拒绝项 missing/incompatible/blocked 写回状态）：
每插件先装合成包与私有依赖 finder（不执行代码）→ `load()` 登记声明式贡献（失败隔离 + `record_crash`）→
插件表经宿主给的 `register_table` 注册（纯数据，不激活也建表，所以 backend 在 `auto_migrate` 之前调 boot）→
激活器记下事件 → 状态推进到 `enabled`；最后 `registries.freeze()`。启动标记：本地在 stage2 前 `enter`，宿主
健康后 `mark_healthy()` 才 `exit`；连续两次残留 → 写 `safe_mode` 并只装内置。一切决定都回到 `BootReport`
（工场页要展示），不只写日志。

## 2026-09-04 · on-demand builtin dependencies (batch 3d.3)

Before stage 1 every builtin passes `ensure_builtin_deps` (probe / install on the local build); a builtin whose on-demand dependencies are unavailable is recorded in `BootReport.deps_missing`, its contributions removed, and the boot continues without it.

Batch 6c: `boot(..., distribution=DistributionResolution | None)`. With a distribution the stage-1 set is its plugin set: builtins it leaves out lose their import-time registrations (`report.excluded_builtins`), bundled path plugins get a synthetic package + private deps via `_prepare_user_plugin` and load fail-fast in stage 1, and `runtime.userPlugins=false` skips the user registry. A resolution with problems raises before anything loads. `report.distribution` names the distribution.

Batch 6c.3: `prepare_bundled_plugins(distribution, store, skip)` is the shared helper (boot and the backend's import-time registration) giving bundled path plugins their synthetic package and deps.
