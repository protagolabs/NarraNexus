---
code_file: src/xyz_agent_context/migration/applier.py
stub: false
last_verified: 2026-07-30
---

## Why it exists

The **write** half of Agent Migration: `apply_plan(db, user_id, plan, agent_id=None)`
executes a `MigrationPlan` onto a NarraNexus agent — create (or reuse) the agent,
then populate awareness / general memory / skills / per-agent url-MCP / **one
Narrative per imported session**. Returns an `ApplyResult` with per-dimension
counts.

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
- **Sessions → Narratives, in-process** (`_import_narrative`, 2026-07-30): one
  Narrative per `plan.narratives` entry. `_summarize_session` makes ONE
  `get_helper_sdk().llm_function` call → the AI fields (description /
  current_summary / topic_hint / topic_keywords / dynamic_summary), with a
  deterministic fallback so a failed/absent LLM never breaks import. Then
  `NarrativeService.create_narrative` (sets name/description) → we overwrite the
  fields create doesn't set → `save_narrative_to_db`. NO agent loop, NO embeddings
  (routing is BM25). The session's turns are `MemoryEngine.retain`'d as
  `observation`/`experience` records `scope_type=narrative`, `scope_id=<narrative>`
  — so imported history is searchable and bound to its thread.
- Every write is best-effort (per-item try/except) so one failure doesn't abort the
  rest; the result records what landed.

## Gotchas

- Local-skill file-copy needs the backend on the SAME machine as the source
  (desktop/local). On cloud it degrades to a marketplace install / unmatched.
- `AgentRepository.id_field == "id"` (autoincrement), so read with `get_agent(agent_id)`,
  NOT `get_by_id`.
- `_summarize_session` calls `get_helper_sdk()` at call time (module-level import,
  invoked inside the fn) so tests can monkeypatch `applier.get_helper_sdk`.
- helper_llm summary uses the user's configured helper slot → its cost is the
  user's (local). Cloud disables the whole import feature, so no cloud summary.
- ⚠️ **Must resolve the owner's provider config first.** Before the narrative
  loop, `apply_plan` calls `resolve_and_set_provider_for_user(user_id, db,
  agent_id=...)` — the migrate route runs OUTSIDE the per-turn context that
  `AgentRuntime.run` sets, so without this `get_helper_sdk()` falls through to the
  platform default OpenAI key (stale → 401) and every summary silently degrades to
  the deterministic fallback. Same defect + fix as every detached background
  helper task (see `providers.resolver.inject_owner_helper_credentials`). Verified
  live: with it, the summary uses the user's anthropic helper (real keywords);
  without it, 401 → fallback.
