---
code_file: src/xyz_agent_context/module/lark_module/descriptor.py
last_verified: 2026-09-04
stub: false
---

# lark_module/descriptor.py — the channel as one record

## Intent

`DESCRIPTOR` / `CHANNEL` for builtin.channels.lark: the trigger, module, credential manager (+ read method), bind/test service shape, credential schema (identity vs secret fields, external id) and UI row that the platform's registry views read. Class references are strings so registering never imports the lark SDK; the trigger's `channel_name` and this name must agree (parity test).

## 2026-09-04 · `meta.storage = "generic"` (batch 4d.2)

The descriptor now declares generic storage like every other channel: the platform has no channel left whose bindings live outside `channel_credentials`.

## 2026-09-04 · bind input declared (batch 4d.3)

`bind_fields`: `app_id`, `app_secret`, `brand` (select feishu|lark, required — no silent default) + optional `owner_email`; the generic route enforces the select before `do_bind` sees it.

## 2026-09-04 · registers its WorkingSource (batch 4e)

`SOURCE = WorkingSource.register("lark")` at import (and the `TriggerType` twin); the package `__init__` imports this module first so `WorkingSource.LARK` exists before the module/trigger class bodies read it. The platform (`hook_schema`) seeds no channel names any more. `meta["agent_instance"]` declares the agent-level LarkModule instance (description/keywords/topic_hint) the instance factory creates for every agent — the factory no longer names Lark.

## 2026-09-04 · no `agent_instance` meta (batch 5b.2)

The module declares its agent-level instance in its own `ModuleConfig`.

## 2026-09-04 · `module_registry` replaces `MODULE_MAP` (batch 5d)

The registry view is the only module table; usages renamed.
