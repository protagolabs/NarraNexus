---
code_file: src/xyz_agent_context/module/telegram_module/descriptor.py
last_verified: 2026-09-04
stub: false
---

# telegram_module/descriptor.py — the channel as one record

## Intent

`DESCRIPTOR` / `CHANNEL` for builtin.channels.telegram: the trigger, module, credential manager (+ read method), bind/test service shape, credential schema (identity vs secret fields, external id) and UI row that the platform's registry views read. Class references are strings so registering never imports the telegram SDK; the trigger's `channel_name` and this name must agree (parity test).
