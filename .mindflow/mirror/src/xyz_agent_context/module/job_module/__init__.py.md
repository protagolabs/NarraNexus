---
code_file: src/xyz_agent_context/module/job_module/__init__.py
last_verified: 2026-08-10
stub: false
---

# job_module/__init__.py — package surface

除 `JobModule` / `JobInstanceService` 与 job channel handler 注册外，PR-8 起还
re-export [[_job_reads]] 的读 helper（`fetch_job_by_id` / `search_jobs_semantic` /
`search_jobs_by_keywords`），供 AgentDataStore seam 的 [[store]] DirectStore 与 backend
[[jobs]] 路由 import **包**、不 reach 私有 `_job_reads` 叶子（同 social/basic_info 先例）。
