# Plugin platform batch 6 — deploy-side lockstep

## 6a (D8): `xyz_agent_context` → `narranexus.platform`

The Python package moved (`module` → `module_system`). **Nothing changes on the
deploy side for this release**: `xyz_agent_context` stays importable as an alias
and the three by-path / `-m` entrypoints the compose file and gate scripts name
still exist as shims:

| Deploy reference | Still works via | Switch to (next release) |
|---|---|---|
| `command: … src/xyz_agent_context/module/module_runner.py mcp` | path shim | `src/narranexus/platform/module_system/module_runner.py mcp` |
| `command: … xyz_agent_context.module.run_worker_supervisor` | `-m` shim | `narranexus.platform.module_system.run_worker_supervisor` |
| `xyz_agent_context.utils.db.sqlite_proxy_server` | `-m` shim | `narranexus.platform.utils.db.sqlite_proxy_server` |
| `scripts/check_trigger_alignment.sh` (`MODULE_DIR`, `ENTRYPOINT`, module names) | the module names import through the alias; the `MODULE_DIR` path check must move now | `NarraNexus/src/narranexus/platform/module_system` + new module names |
| `scripts/check_executor_clis.sh` (`SKILLS_DIR` under `src/xyz_agent_context/…`) | path no longer exists | `src/narranexus/platform/…` |

Do the switch in the release AFTER the app release that ships the alias, then
delete `src/xyz_agent_context/` (the alias package + shims) app-side. The alias
emits one `DeprecationWarning` per process; grep the app logs for
"xyz_agent_context is an alias" to find any deploy-side reference left.
