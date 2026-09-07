---
code_file: src/narranexus/platform/channel/contributions.py
last_verified: 2026-09-07
stub: false
---

# src/narranexus/platform/channel/contributions.py

## 2026-09-07 — descriptor-derived contributions

contributions_from(descriptor, plugin_id) yields a channel plugin's MODULES and TRIGGERS from the one ChannelDescriptor it already writes (module_ref / trigger_ref), so adding a channel is one descriptor, not six table rows. module_contribution / trigger_contribution are the two shapes every module plugin's contribution.py uses.
