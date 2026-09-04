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
