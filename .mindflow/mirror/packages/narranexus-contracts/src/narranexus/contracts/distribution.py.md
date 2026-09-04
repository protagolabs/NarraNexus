---
code_file: packages/narranexus-contracts/src/narranexus/contracts/distribution.py
last_verified: 2026-09-04
stub: false
---

# contracts/distribution.py — narranexus-dist.json

The distribution contract (spec section 19.3): `DistributionSpec` (id, displayName, engine range, `plugins` id → `PluginRef` {range | path}, excludes, branding, `auth`, defaults, `runtime` {userPlugins, deployment}, targets, bindings). `parse_distribution()` normalises plugin refs (a string is a range, an object with `path` is bundled) and rejects: bad ids, excludes overlapping plugins, cloud deployment with userPlugins, `bindings[kernel.auth]` disagreeing with `auth`, unknown fields. `DistributionError` is the single error type. Parsing only; resolution is in the kernel.
