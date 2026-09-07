---
code_file: plugins/builtin.job/src/narranexus_plugins/job_module/routes_dashboard.py
last_verified: 2026-09-04
stub: false
---

# dashboard/jobs.py — builtin.job's dashboard controls

## Intent

`POST /api/dashboard/jobs/{id}/pause|resume` and `PUT .../schedule` delegate to `job_module.job_recovery` (the portable state-machine core), so they belong to builtin.job: split out of `dashboard/routes.py` in batch 3c.5 and provided through `backend.routes` (`ROUTES`, prefix `/api/dashboard`). Auth/ownership reuse the dashboard's `_resolve_viewer` / `_assert_agent_visible`; behaviour and paths are byte-identical (route snapshot unchanged). Disabling builtin.job removes these controls while the dashboard's reads and the SQL-only retry stay.

## 2026-09-07 — moved into the plugin package

This router is the plugin's own contribution (`narranexus_plugins.job_module.routes_dashboard:ROUTES`), not a file under `backend/routes/` that the plugin's manifest reached out to: the package is self-contained (a distribution that leaves the plugin out has no dead router in the wheel), and the backend no longer imports the plugin's private modules to serve it. Builtin routers keep their absolute prefixes (`/api/...`); third-party plugins live under `/api/x/<id>` — the one deliberate difference, recorded in docs/API_POLICY.md.
