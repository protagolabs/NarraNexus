---
code_file: src/narranexus/platform/module_system/telegram_module/descriptor.py
last_verified: 2026-09-04
stub: false
---

# telegram_module/descriptor.py — the channel as one record

## Intent

`DESCRIPTOR` / `CHANNEL` for builtin.channels.telegram: the trigger, module, credential manager (+ read method), bind/test service shape, credential schema (identity vs secret fields, external id) and UI row that the platform's registry views read. Class references are strings so registering never imports the telegram SDK; the trigger's `channel_name` and this name must agree (parity test).

## 2026-09-04 · generic store as the source of truth (batch 4d.1)

`meta.storage = "generic"`: the manager persists in `channel_credentials`, so the dual-write mirror skips this channel.

## 2026-09-04 · bind input declared (batch 4d.3)

`bind_fields`: `bot_token` (secret) + optional `owner_username` — the `do_bind` keyword arguments the generic bind passes through.

## 2026-09-04 · registers its WorkingSource (batch 4e)

`SOURCE = WorkingSource.register("telegram")` at import (and the `TriggerType` twin); the package `__init__` imports this module first so `WorkingSource.TELEGRAM` exists before the module/trigger class bodies read it. The platform (`hook_schema`) seeds no channel names any more.

## 2026-09-04 · `module_registry` replaces `MODULE_MAP` (batch 5d)

The registry view is the only module table; usages renamed.
