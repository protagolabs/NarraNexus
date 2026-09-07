---
code_file: src/narranexus/kernel/plugins/catalog.py
last_verified: 2026-09-07
stub: false
---

# kernel/plugins/catalog.py — the slot catalog

`slot_catalog(registries, domain=None)`: every slot grouped by domain (`DOMAINS` order: kernel, prompt, turn, model, agent, ingress, backend, content, ui, builtin) with contract, arity, default, distribution-only flag, candidates (owners), contributions and the live binding (provider + layer/origin, or ordered providers). `toml_template()` renders a commented `narranexus.toml`. Used by `narranexus slots`, the docs generator, `GET /api/plugin-factory/slots` and the agent's awareness tools.
