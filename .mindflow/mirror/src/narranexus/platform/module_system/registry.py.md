---
code_file: src/narranexus/platform/module_system/registry.py
last_verified: 2026-09-04
stub: false
---

# module/registry.py — ModuleRegistry / module_registry

## Intent

The platform's ONLY way to name a module (plugin platform batch 5d): a read-only `Mapping[str, type]` over the kernel `agent.capabilities.modules` registry, plus `meta(name)` / `owner_of(name)` for the contribution's plugin id. `MODULE_MAP` (the table) and the package's lazy class re-exports are gone — a builtin and a plugin module are both just contributions, a builtin disabled through registry.json is simply absent, and code that needs a module class asks `module_registry[name]` (or `ModuleRegistry(registries)` for a test's own registries). Builds are cached per registry generation so hot paths pay a dict lookup; a module whose import fails is absent, not fatal.
