---
code_file: plugins/builtin.frameworks.nexus_power/src/narranexus_plugins/frameworks_nexus_power/contribution.py
last_verified: 2026-09-04
stub: false
---

# builtin.frameworks.nexus_power — contribution.py

`CONTRIBUTION` for the default framework `nexus_power`: a factory that builds the NexusPower adapter (no external SDK, so `FrameworkInstall` declares nothing to install). Named by the builtin manifest and registered lazily by `loop.driver.ensure_builtin_frameworks()` (batch 6b.2b).
