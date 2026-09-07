---
code_file: conftest.py
last_verified: 2026-09-07
stub: false
---

# conftest.py

## 2026-09-07 — repo-root pytest configuration

Loads the builtins into the process registries once at collection for the backend role (not frozen) — for tests/ and plugins/*/tests alike, since registration happens only at boot and some modules resolve a registry entry at import.
