---
code_file: src/xyz_agent_context/bootstrap/provision.py
stub: false
last_verified: 2026-08-10
---

## Why it exists

The canonical "make a brand-new agent usable" sequence, extracted as one
shared seam (PR-2 pre-open review #3). It existed as THREE drifting copies —
auth.py's create_agent route (complete, the semantic source), the
create_agent MCP tool closure (a HALF copy that skipped default-skill
install), and PR-2's own social-network create-agent route. The MCP closure's
gap meant agent-created agents shipped with no marketplace skills and, before
PR-2, no social/basic-info/bus instances and no peer-discovery entry — the
"ask agent X came back empty" P1.

## The sequence

add_agent → InstanceFactory.create_agent_level_instances → sync_agent_discovery
→ apply_bootstrap(profile) → SkillMarketplaceService.install_defaults
(fire-and-forget; the step review #3 named as missing from the MCP copy) →
seed caller-supplied awareness onto the factory-built AwarenessModule instance.
Each step is best-effort (try/except) mirroring auth.py's semantics; the agent
row exists either way and per-turn hooks re-sync.

## Convergence

All THREE call sites now delegate to this seam: auth.py's create_agent route,
the create_agent MCP tool closure, and the social-network create-agent HTTP
route. auth.py stays the SEMANTIC SOURCE (the seam mirrors what its route
established) and keeps only the non-shared parts — user-existence validation,
team assignment (#43), and the CreateAgentResponse shape — outside the seam
call.

## 上下游

- 调用方: [[_social_mcp_tools]] create_agent 闭包、[[social_network]] 路由
  create-agent 端点
- 依赖: [[agent_repository]] / InstanceFactory / agent_discovery_sync /
  [[profiles]] apply_bootstrap / SkillMarketplaceService
- 语义来源: [[auth]] create_agent 路由(已改调 seam,仅留 team #43 + response)
