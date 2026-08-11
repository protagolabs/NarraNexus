---
code_file: src/xyz_agent_context/module/job_module/__init__.py
last_verified: 2026-08-10
stub: false
---

# job_module/__init__.py — package surface

除 `JobModule` / `JobInstanceService` 与 job channel handler 注册外，re-export
[[_job_reads]] 的读 helper（PR-8）与 [[_job_writes]] 的 `update_job_from_args`（PR-8b），
供 AgentDataStore seam 的 [[store]] DirectStore、agent-scoped [[agents/jobs]] 路由、
以及前端 [[jobs]] 路由 import **包**、不 reach 私有叶子（同 social/basic_info 先例）。
