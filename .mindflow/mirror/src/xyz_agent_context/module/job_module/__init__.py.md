---
code_file: src/xyz_agent_context/module/job_module/__init__.py
last_verified: 2026-08-11
stub: false
---

# job_module/__init__.py — package surface

除 `JobModule` / `JobInstanceService` 与 job channel handler 注册外，re-export
[[_job_reads]] 的读 helper（PR-8）与 [[_job_writes]] 的全部写 helper——
`update_job_from_args`（PR-8b）+ `create_job_from_args` / `pause_job_from_args` /
`cancel_job_from_args`（2026-08-11，job 三写迁 seam）——供 AgentDataStore seam 的
[[store]] DirectStore、agent-scoped [[agents/jobs]] 路由、以及前端 [[jobs]] 路由
import **包**、不 reach 私有叶子（同 social/basic_info 先例）。
