---
code_file: backend/onboarding/__init__.py
last_verified: 2026-08-19
stub: false
---

# __init__.py — profile registration side effect + login-hook exports

## Why it exists

Two jobs: (1) re-export `ensure_guide_agent` / `is_guide_agent_enabled` /
`is_backfill_enabled` for the login hooks; (2) **register the "onboarding"
bootstrap profile** — the `from backend.onboarding import profile as
_profile  # noqa: F401` line is a load-bearing side-effect import, exactly
like Arena's profile registering on import of its provisioning service.

## Gotchas

- **Do not delete the `_profile` import as "unused".** Removing it raises
  nothing anywhere: `get_profile("onboarding")` silently falls back to the
  "default" profile on an unknown name, so every guide agent would render
  the generic blank-slate first-run (wrong greeting, wrong playbook) with no
  error in any log. The import IS the registration.
- The login hooks import THIS package (not `provisioning` directly), so the
  registration always precedes any `apply_bootstrap` call.
