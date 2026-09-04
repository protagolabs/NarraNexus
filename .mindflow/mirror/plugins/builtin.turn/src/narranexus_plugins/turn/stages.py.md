---
code_file: plugins/builtin.turn/src/narranexus_plugins/turn/stages.py
last_verified: 2026-09-04
stub: false
---

# builtin.turn — stages.py

Contribution tuples per stage slot — `INGRESS`, `RECALL`, `COMPOSE`, `ASSEMBLE`, `ACT`, `COMMIT`, `REFLECT` — each wrapping the default strategy of that stage as a `Contribution`; `STRATEGIES` maps slot path to tuple. The builtin manifest names these under `turn.pipeline.<stage>`; `platform.turn.stages.ensure_registered()` has the kernel register them when a slot is empty.
