---
code_file: src/xyz_agent_context/utils/host_hooks.py
last_verified: 2026-09-04
stub: false
---

# utils/host_hooks.py — fire a host event from platform code

## Intent

`call_host_hook(name, **payload)` runs every `backend.hooks` implementation of a host event on the kernel registries (importing `xyz_agent_context.module` first so builtin hooks exist regardless of import order). Used by the rename transaction, the bundle importer, the Manyfold sync route and `backend/host_events`. Results come back in registration order; errors per owner, never raised.
