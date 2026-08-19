---
code_file: backend/onboarding/__init__.py
last_verified: 2026-08-19
stub: false
---

# __init__.py — deliberately import-free package marker

## Why it exists (and why it is empty)

`naming.py` is also consumed by `backend/integrations/arena/
arena_onboarding.py`, a pure-HTTP module whose docstring promises "no DB, no
settings" — an eager re-export of `provisioning` here would drag
AsyncDatabaseClient and the whole provisioning stack into every
`import backend.onboarding.naming`. So the package `__init__` carries only a
docstring, and consumers import their module directly: the login hooks use
`backend.onboarding.provisioning`, Arena uses `backend.onboarding.naming`.

## Gotchas

- **The "onboarding" profile registration rides `provisioning`'s import**
  (`import backend.onboarding.profile  # noqa` at its import block), NOT this
  file. Anyone re-adding convenience re-exports here must keep naming's
  import cheap — that is the one invariant this emptiness protects.
- Removing the registration import in provisioning.py raises nothing:
  `get_profile("onboarding")` silently falls back to the default profile,
  and every guide agent renders the generic blank-slate first-run. The
  integration test (bilingual greeting assertion) is what goes red.
