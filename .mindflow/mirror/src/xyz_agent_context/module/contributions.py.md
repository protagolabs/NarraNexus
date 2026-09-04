---
code_file: src/xyz_agent_context/module/contributions.py
last_verified: 2026-09-04
stub: false
---

## 2026-09-04（批 3c.1）— 内置模块贡献表：module_registry / MCP 端口 / 常驻列表的唯一来源

一行一个内置模块（包名、类名、插件 id、核心端口或渠道读类属性、always_load）。`load_class` 从
`<pkg>.<pkg>` 叶子模块取类（chat/awareness/basic_info/general_memory 的 `__init__` 刻意不 re-export 类，
首版从包取属性全部失败实锤）。`CONTRIBUTIONS` 每模块一个对象（manifest 驱动注册幂等靠对象同一性）；
`PLUGIN_<ID>` 常量是 manifest 指向的符号；`register_all` 在 `module/__init__` import 时登记。

## 2026-09-04 · ingress triggers (batch 3c.3)

Also the home of `TRIGGER_SPECS` (six channel triggers, the `jobs` clock as host=workers with its historical kwargs, the `a2a` server as host=api) with `TRIGGERS_<ID>` per-plugin tuples the manifests name, and `HOOK_SPECS` (builtin.chat's greeting hook). `register_all` registers modules, triggers and hooks so processes that never run a manifest boot see the same picture; `channel_trigger_specs()` is the static registration intent the alignment test checks.

## 2026-09-04 · data-access providers (batch 3c.4)

`DATA_ACCESS_SPECS` (owner → `<module>/data_access.py:DATA_ACCESS`) registered by `register_all` alongside modules/triggers/hooks; `_resolve_symbol` is the shared lazy import.

## 2026-09-04 · services + host hooks (batch 3c.6)

`HOOK_SPECS` covers job (runnability), awareness (identity), six channels (credential export); `SERVICE_SPECS` exposes skills/job services; `register_all` registers both (services skipped when already exposed).

## 2026-09-04 · channels as descriptors (batch 4a)

`CHANNEL_SPECS` (seven descriptors) registered into `ingress.channels` by `register_all`, which also registers each inbound channel's `WorkingSource`.

## 2026-09-04 · generic channel credentials (batch 4b)

`register_all` registered the credential mirror on manager-backed channels during the 4b dual-write phase; since 4d.2 every builtin manager persists in the generic store directly and the mirror is gone.

## 2026-09-04 · no mirror installation (batch 4d.2)

`install_manager_mirrors` no longer exists; `register_all` registers channels, triggers, data-access, hooks and services only. All seven `CHANNEL_SPECS` descriptors declare `storage: generic`.

## 2026-09-04 · no port on the spec (batch 5a)

`ModuleSpec` is (package, class, plugin id, always_load); `mcp_port` / `port()` and the contribution meta's `mcp_port` are gone — the host mounts by `server_name`.

## 2026-09-04 · `ModuleSpec` is (package, class, plugin id) (batch 5b)

`always_load` moved to the module's own `ModuleConfig`; the registry meta carries `plugin_id` / `channel` only.

## 2026-09-04 · `module_registry` replaces `MODULE_MAP` (batch 5d)

The registry view is the only module table; usages renamed.
