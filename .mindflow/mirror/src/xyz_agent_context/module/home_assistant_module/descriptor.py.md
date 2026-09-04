---
code_file: src/xyz_agent_context/module/home_assistant_module/descriptor.py
last_verified: 2026-09-04
stub: false
---

# home_assistant_module/descriptor.py — credentials-only channel

## Intent

Home Assistant has no inbound transport (`transport="none"`, no trigger) but the data-access channel seam serves its credential manager, so it is a descriptor too; it registers no WorkingSource.
