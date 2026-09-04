---
code_file: plugins/builtin.channels.narramessenger/src/narranexus_plugins/narramessenger_module/descriptor.py
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

## 2026-09-04 · registers its WorkingSource (batch 4e)

`SOURCE = WorkingSource.register("narramessenger")` at import (and the `TriggerType` twin); the package `__init__` imports this module first so `WorkingSource.NARRAMESSENGER` exists before the module/trigger class bodies read it. The platform (`hook_schema`) seeds no channel names any more. `meta["contact_key"] = "matrix"`: the key this channel's ids live under in `contact_info.channels`, read by `channel_contact_utils.contact_channel_keys`.

## 2026-09-04 · `module_registry` replaces `MODULE_MAP` (batch 5d)

The registry view is the only module table; usages renamed.
