---
code_file: src/narranexus/cli/bindings_cli.py
last_verified: 2026-09-07
stub: false
---

# cli/bindings_cli.py — slots / bind / unbind

`booted_registries()` boots builtins + the user registry on private registries and resolves bindings; `render_catalog` prints the catalog; `bind(regs, path, slot, providers)` validates (slot exists, not distribution-only, providers registered, arity) and rewrites only the `[bindings]` table (undone if the file then fails to resolve); `unbind`; `read/write_bindings_table` keep other tables verbatim.
