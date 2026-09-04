---
code_file: backend/routes/dashboard/jobs.py
last_verified: 2026-09-04
stub: false
---

# dashboard/jobs.py — builtin.job's dashboard controls

## Intent

`POST /api/dashboard/jobs/{id}/pause|resume` and `PUT .../schedule` delegate to `job_module.job_recovery` (the portable state-machine core), so they belong to builtin.job: split out of `dashboard/routes.py` in batch 3c.5 and provided through `backend.routes` (`ROUTES`, prefix `/api/dashboard`). Auth/ownership reuse the dashboard's `_resolve_viewer` / `_assert_agent_visible`; behaviour and paths are byte-identical (route snapshot unchanged). Disabling builtin.job removes these controls while the dashboard's reads and the SQL-only retry stay.
