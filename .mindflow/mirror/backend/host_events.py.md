---
code_file: backend/host_events.py
last_verified: 2026-09-04
stub: false
---

# backend/host_events.py — host events from routes

## Intent

`emit_host_event` (never raises) and `notify_user_runnability_changed` — login, quota top-up and provider/slot saves fire `onDidChangeUserRunnability` instead of importing job_recovery; builtin.job's hook does the re-arm.
