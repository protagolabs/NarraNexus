---
code_file: src/xyz_agent_context/utils/plugin_services.py
last_verified: 2026-09-04
stub: false
---

# utils/plugin_services.py — platform accessors for builtin services

## Intent

Thin wrappers over `KERNEL_REGISTRIES.services` + `service_refs` (`skill_workspace`, `job_instances`, `try_job_run_once`). They import `xyz_agent_context.module` first so the builtins' `register_all` has run whatever the import order; `require` fails loud (UnknownEntry) when the owning builtin is disabled, `try_*` returns None for callers with a degraded path (Manyfold run-job → `jobs_unavailable`).
