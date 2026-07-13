---
code_file: src/xyz_agent_context/artifact/__init__.py
last_verified: 2026-07-13
stub: false
---

# __init__.py — shared artifact package marker

## Why it exists

Marks `xyz_agent_context/artifact/` as a **module-independent, shared** package.
The pointer-model registration core ([[registration]]) lives here — NOT inside
any Module — so every caller (the common_tools `register_artifact` MCP tool
[[artifact_tool]], OfficeModule's [[_office_mcp_tools]], the backend routes
[[agents_artifacts]], bootstrap welcome artifacts [[profiles]]) reaches it
through the shared layer instead of importing another Module's private
`_*_impl/` (binding rule #3).
