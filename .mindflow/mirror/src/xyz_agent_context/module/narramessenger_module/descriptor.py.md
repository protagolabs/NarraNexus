---
code_file: src/xyz_agent_context/module/narramessenger_module/descriptor.py
last_verified: 2026-09-04
stub: false
---

# narramessenger_module/descriptor.py — the channel as one record

## Intent

`DESCRIPTOR` / `CHANNEL` for builtin.channels.narramessenger: the trigger, module, credential manager (+ read method), bind/test service shape, credential schema (identity vs secret fields, external id) and UI row that the platform's registry views read. Class references are strings so registering never imports the narramessenger SDK; the trigger's `channel_name` and this name must agree (parity test).

## 2026-09-04 · `meta.storage = "generic"` (batch 4d.2)

Bindings live in `channel_credentials`; no mirror, no bespoke table.

## 2026-09-04 · bind input declared (batch 4d.3)

`bind_fields`: the single `bind_command` (pasted link/command, not stored) — the stored schema is the Matrix identity `do_bind` derives from it. `unbind_service=True` with `bind_takes="db"` routes unbind through `do_unbind(db, agent_id)` (gateway-side unbind included).
