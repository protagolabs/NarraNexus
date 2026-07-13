---
code_file: src/xyz_agent_context/module/office_module/_office_impl/__init__.py
last_verified: 2026-07-13
stub: false
---

# __init__.py — OfficeModule private-impl marker

## Why it exists

Marks the private implementation package for OfficeModule. Its contents
([[officecli_client]], [[_office_command_security]], [[_office_mcp_tools]]) are
**not re-exported** outside the module — the `_`-prefix signals "internal;
reach it only through [[office_module]]" (project convention: private
implementation lives under `_*_impl/`, never re-exported).
