"""
@file_name: __init__.py
@author: rujing.yan
@date: 2026-07-13
@description: Shared, module-independent artifact registration infrastructure.

The pointer-model registration core (`registration.register_artifact`) lives
here — NOT inside any Module — so every caller (the common_tools
`register_artifact` MCP tool, the OfficeModule preview flow, bootstrap welcome
artifacts, and the backend REST routes) reaches it through the shared layer
instead of importing another Module's private `_*_impl/` (binding rule #3).
"""
