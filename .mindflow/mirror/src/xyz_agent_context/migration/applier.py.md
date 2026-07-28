---
code_file: src/xyz_agent_context/migration/applier.py
stub: false
last_verified: 2026-07-21
---

## Why it exists

The **write** half of Agent Migration: `apply_plan(db, user_id, plan, agent_id=None)`
executes a `MigrationPlan` onto a NarraNexus agent — create (or reuse) the agent,
then populate awareness / general memory / skills / per-agent url-MCP. Returns an
`ApplyResult` with per-dimension counts + the pass-through `narrative_instruction`.

## Design decisions

- **Reuses existing repos/factories** (no HTTP coupling): `AgentRepository.add_agent`
  + `InstanceFactory.create_agent_level_instances` (provisions AwarenessModule et
  al.); awareness via `InstanceAwarenessRepository.upsert`; memory via
  `MemoryEngine.retain` per fact (no batch API exists); url-MCP via
  `MCPRepository.add_mcp`. `user_id` is passed in — the one thing that is
  request-bound (`resolve_current_user_id`) stays in the route.
- **Default NarraNexus skills**: a newly-created agent gets the same `is_default`
  set (netmind-vision, officecli, ...) a normally-created agent gets, via
  `SkillMarketplaceService.install_defaults` — the migration path bypasses the
  `POST /api/auth/agents` route that normally fires this, so it must do it here
  or imported agents lack the built-in skills. Runs BEFORE the imported skills so
  a same-name imported skill still wins (faithful repro overwrites the default
  copy). Only for `created=True`; degrades to a no-op when the registry is
  unreachable (desktop offline). Not fire-and-forget here (unlike the route) —
  apply is awaited and reports counts, so defaults are awaited and surfaced in
  `default_skills_installed`.
- **Faithful-reproduction skills (Owner)**: a skill with a `local_path` is COPIED
  verbatim into `agent_workspace_path(agent_id,user_id)/skills/<name>/` — migration
  reproduces the ORIGINAL agent, not a same-name marketplace skill (which may be a
  different implementation). Marketplace install is only the fallback for a
  name-only skill.
- **Narrative is NOT executed here** — it is agent-driven. `narrative_instruction`
  is returned for the caller to send as the agent's first turn so it self-authors a
  Narrative via `create_narrative`.
- Every write is best-effort (per-item try/except) so one failure doesn't abort the
  rest; the result records what landed.

## Gotchas

- Local-skill file-copy needs the backend on the SAME machine as the source
  (desktop/local). On cloud it degrades to a marketplace install / unmatched.
- `AgentRepository.id_field == "id"` (autoincrement), so read with `get_agent(agent_id)`,
  NOT `get_by_id`.
