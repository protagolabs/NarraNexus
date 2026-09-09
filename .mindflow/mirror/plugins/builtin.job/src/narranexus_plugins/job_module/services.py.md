---
code_file: plugins/builtin.job/src/narranexus_plugins/job_module/services.py
last_verified: 2026-09-04
stub: false
---

# job_module/services.py — builtin.job services

## Intent

Exposes `jobs.instances` (a `JobInstanceService` factory over the caller's db; onboarding and Arena provisioning create jobs through it) and `jobs.run_once` (`run_once.run_job_once`; the Manyfold sync route runs a job on demand through it).
