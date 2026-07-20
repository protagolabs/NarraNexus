---
code_file: src/xyz_agent_context/skill_marketplace_service.py
last_verified: 2026-07-21
stub: false
---

# skill_marketplace_service.py

Service protocol layer (public façade) for the Skill Marketplace — the ONLY
entry point backend routes and MCP tools use. Hides the deployment split in
exactly one place: `_is_registry_host()` (cloud → in-process DB registry;
desktop → RemoteMarketplaceSource against the cloud API). Installs always
run locally through InstallPipeline against this host's workspace,
whichever side the catalog lives on.

## Design decisions

- **Mode decision is centralized here** — `install()` passes an explicit
  `marketplace_source` to the pipeline instead of letting the pipeline
  re-derive the mode (was a real bug caught by tests: two call sites
  deciding independently).
- `_annotate_installed` reads the workspace filesystem (SkillModule +
  `.skill_meta.json` versions), consistent with disk-is-truth; it does NOT
  consult the audit DB.
- `check_updates` (agent-scoped) builds the installed list from disk, then
  asks the registry (local or remote batch endpoint).
- Cheap to construct — one instance per request/tool call, no caching.
