---
code_file: src/xyz_agent_context/_skill_marketplace_impl/registry.py
last_verified: 2026-07-21
stub: false
---

# registry.py

Marketplace registry: publish pipeline, catalog queries, and the two install
sources. The DB catalog is the ONLY directory truth — the v1.0 design's S3
`registry-index.json` was deliberately dropped (one directory source = no
index/DB drift, kills old "风险三").

## Publish flow

extract (reuses SkillModule.extract_skill_package with a dummy `__registry__`
instance — same zip-safety checks) → manifest.json authoritative, minimal
manifest synthesized from SKILL.md frontmatter when absent, version REQUIRED
→ scan gate (`PublishRejectedError` carries the report for the 422 body) →
artifact + manifest uploaded at `{id}/{version}/…` → catalog + scan rows.
Artifacts are immutable: republishing the same id@version updates catalog
metadata but the store object is overwritten with identical content only if
the hash matches what's recorded (enforced socially — hash check happens at
install).

## Install sources

`LocalMarketplaceSource` (cloud: repo + store in-process; bumps downloads on
record_install) vs `RemoteMarketplaceSource` (desktop: cloud HTTP API;
download counter is bumped server-side by /download, record_install no-ops).
Both expose `resolve_and_download` for the InstallPipeline. The MODE DECISION
lives in `skill_marketplace_service.py`, not here.

`RemoteMarketplaceSource` trusts `X-Skill-Version` / `X-Package-Hash`
response headers so the hash verify in the pipeline checks against what the
registry claims, not what the body happens to be.
