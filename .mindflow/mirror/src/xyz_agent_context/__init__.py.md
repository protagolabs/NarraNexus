---
code_file: src/xyz_agent_context/__init__.py
last_verified: 2026-09-04
stub: false
---

# xyz_agent_context/__init__.py — the one-release alias of narranexus.platform (D8)

## Intent

Batch 6a moved every domain package to `narranexus.platform` (`module` → `module_system`). This package exists so the old import spelling keeps working for exactly one release: a `sys.meta_path` finder (`_AliasFinder`, inserted first) answers every `xyz_agent_context.<path>` with the SAME module object as `narranexus.platform.<path>` (`_AliasLoader.create_module` returns the imported target, so monkeypatches, `isinstance` and singletons agree), the root re-exports the platform root, and one `DeprecationWarning` per process names the replacement. Real files exist only where something runs a module by path or `-m` (the three entrypoint shims); everything else resolves through the finder.

## Removal

Delete `src/xyz_agent_context/` (and the `packages` entry in pyproject) in the release after the deploy repo switched its compose entrypoints — `docs/PLUGIN_BATCH6_DEPLOY_LOCKSTEP.md` lists them.
