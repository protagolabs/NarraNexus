---
code_file: src/xyz_agent_context/module/data_access/__init__.py
stub: false
last_verified: 2026-08-10
---

## Why it exists

Public surface of the data-access seam (blueprint P0): the protocol
(`AgentDataStore`), both implementations and the composition root
(`get_agent_data_store`). MCP tools import from HERE, never from the
submodules — the seam's whole point is that a tool cannot tell (and must not
choose) which transport it got.
