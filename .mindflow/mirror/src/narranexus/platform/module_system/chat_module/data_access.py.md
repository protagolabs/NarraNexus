---
code_file: src/narranexus/platform/module_system/chat_module/data_access.py
last_verified: 2026-09-04
stub: false
---

# chat_module/data_access.py — this builtin's AgentDataStore bodies

## Intent

Provides `agent.capabilities.data_access` contributions: get_chat_history over `fetch_chat_history`, the same function the chat-history twin route calls. Module internals are imported inside each handler so registering the contribution (at `register_all` / manifest load) costs nothing; the parity rejects, clamps and never-raise wrapping stay in `DirectStore`.
