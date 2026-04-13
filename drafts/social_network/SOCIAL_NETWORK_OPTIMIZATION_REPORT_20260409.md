# Social Network Module Optimization — Progress Report

**Date:** 2026-04-09
**Branch:** `feat/social_network_improve_2026_04_09`
**Test Agent:** `agent_fc56ba8c26f3` (social agent 1)
**Reference Agent:** `agent_f45c4f306db4` (Hering) — used for initial diagnosis

---

## 1. The Problem

Hering's social network had **266 entities**. 161 were non-social junk:
- Competitions: "Bitcoin Forum", "Art Contest", "Daily Lottery"
- Platforms/APIs: "Arena42", "NexusMatrix API", "TodoWrite"
- Concepts: "Bitcoin", "machine learning", "FOMC", "Jevons Paradox"
- Sports teams: "Lakers", "Real Madrid", "Crystal Palace"
- File paths: "skills/arena/credentials.json"
- System tools: "send_message_to_user_directly"

Real agents duplicated 2-4x each: `alpha4` / `agent_5a22e015f115` / `@agent_5a22e015f115:localhost` all as separate entities.

Only 1 entity (`hongyitest`) had `interaction_count > 0`. The module was **unusable** — search returned noise, no relationships between entities, no way to distinguish people from concepts.

**Root cause:** The extraction prompt treated every noun as an entity. No dedup pipeline. Search auto-detect routed name queries to tag-only search (never matched entity names).

---

## 2. Principles We Agreed On

Grounded in cognitive science research on social memory (deep research artifact: "Why Your AI Agent Needs a Separate Brain for People"):

1. **Social network stores only social entities** — humans, agents, groups (things with agency that can be interacted with). Non-social things (competitions, concepts, platforms) are **keywords on people**, not entities themselves.

2. **Familiarity tiers** — Distinguish `direct` (actually interacted with) from `known_of` (mentioned by others). Mirrors the brain's dual-process memory: recollection vs. familiarity (Yonelinas, 1994).

3. **Cross-system identity via aliases** — Same entity may appear under different IDs (internal ID, Matrix ID, platform agent ID). Aliases enable deduplication across systems.

4. **Flat structure, not graph** — We keep the existing flat DB table. No EventNodes, ConceptNodes, or graph edges for now. Keywords on entities serve as the association layer for topic-to-person retrieval.

5. **Dedup pipeline with human judgment** — Name+alias exact match → vector similarity with threshold → LLM decides merge-or-not. False negatives preferred over false merges.

---

## 3. Changes Implemented

### Phase 0: Schema & DB
- **New fields:** `aliases` (JSON), `familiarity` (VARCHAR, "direct"/"known_of")
- **New entity_type:** "group" added alongside "user"/"agent"
- **Rename:** Python field `tags` → `keywords` (DB column unchanged for backward compat)
- **DB migration:** ALTER TABLE applied for new columns

**Files:** `entity_schema.py`, `create_instance_social_entities_table.py`, `social_network_repository.py`

### Phase 1: Extraction Prompt Rewrite
- **Social-only extraction:** Prompt defines social entity by principle — must be individually identifiable, have agency, have a proper name. Generic role references, categories, and plurals rejected.
- **Keywords on people:** Non-social context (competitions, platforms, topics) attached as keywords on the associated person
- **Aliases extraction:** "alpha4 (@agent:localhost)" → ONE entity with alias
- **Familiarity classification:** `direct` for conversation participants, `known_of` for mentioned-only

**Files:** `prompts.py`, `_entity_updater.py` (ExtractedEntity model)

### Phase 2: 3-Stage Dedup Pipeline
Runs in post-hook for each extracted entity:
```
Stage 1: Exact name+alias match (repo.search_by_name_or_alias)
  → 1 match → UPDATE directly
  → multiple → Stage 3 (LLM decides)
  → 0 → Stage 2

Stage 2: Vector similarity search (threshold=0.7, topK=3)
  → candidates found → Stage 3
  → no candidates → CREATE NEW

Stage 3: LLM merge decision (MERGE or CREATE_NEW)
  Prompt: compare candidate vs existing entity, MERGE if same, CREATE_NEW if different
  → MERGE → update description, keywords, aliases
  → CREATE_NEW → create new entity
```

**Files:** `social_network_module.py` (_process_mentioned_entities), `_entity_updater.py` (decide_merge_or_create, DedupDecision), `prompts.py` (DEDUP_MERGE_DECISION_INSTRUCTIONS)

### Phase 3: Search Auto-Detect Fix
- **Before:** Auto-detect routed non-ID queries to `search_by_tags` (JSON_SEARCH on tags column) — never matched entity names
- **After:** Routes to `keyword_search` which searches entity_name + entity_description + tags/keywords + aliases via LIKE

**Files:** `social_network_module.py` (_search_entities)

### Phase 4: Post-Hook Performance
- **Parallelized:** `asyncio.gather` for `summarize_new_entity_info` + `extract_mentioned_entities` (independent LLM calls)
- **Eliminated:** 3 redundant `repo.get_entity` calls in post-hook (was fetching same entity 3 times)
- **Fixed:** `should_update_persona` now checks user input (not agent output) for change signals

**Files:** `social_network_module.py` (hook_after_event_execution)

### Phase 5: tags→keywords Rename
Mechanical rename across all consuming code. DB column stays `tags`, only Python field changes. MCP tools accept both `"tags"` and `"keywords"` for backward compat.

**Files:** `social_network_module.py`, `_social_mcp_tools.py`, `_entity_updater.py`, `test_persona.py`, `_job_context_builder.py`

### Self-Exclusion (added during testing)
- Agent's own name and ID passed to extraction as explicit exclusions
- Both LLM prompt and post-filter exclude self-references
- Prevents the agent from extracting itself as a social entity

**Files:** `social_network_module.py`, `_entity_updater.py`

### Frontend: Familiarity & Aliases Display
- **API schema:** Added `aliases`, `familiarity`, `keywords` to `SocialNetworkEntityInfo`
- **Backend routes:** All 3 endpoints (get, list, search) now return new fields
- **EntityCard:** Familiarity badge in header (green "Direct" / gray "Known of"), aliases section in expanded details

**Files:** `api_schema.py`, `agents_social_network.py`, `api.ts`, `EntityCard.tsx`

---

## 4. Bug Fixes Along the Way

### Step 1 Narrative Crash (blocking all job-triggered social extraction)
**Bug:** `UnboundLocalError: cannot access local variable 'selection_result'` in `step_1_select_narrative.py:246`
**Cause:** When job uses forced narrative (loads successfully), `selection_result` is never assigned. Line 246 `scores = selection_result.scores` crashes.
**Fix:** Initialize `selection_result = None` before if/else, guard with `if selection_result else {}`.
**File:** `step_1_select_narrative.py`

### Background Hook Logs Lost
**Bug:** Post-hook social extraction logs never appeared in agent log files. Only `hook_data_gathering` (pre-conversation) was visible.
**Cause:** `LoggingService.cleanup()` calls `logger.remove(handler_id)` but with `enqueue=True`, the async queue hasn't flushed yet. Pending log records lost.
**Fix:** Added `async_cleanup()` method that calls `await logger.complete()` before removing handler. Background task now uses `await _logging_service.async_cleanup()`.
**Files:** `logging_service.py`, `agent_runtime.py`

### Claude Code Login Check
**Bug:** `run.sh` configure step couldn't detect Claude Code login — always said "not logged in".
**Cause:** Login check only read `~/.claude/.credentials.json` (legacy v1.x format). Claude Code v2.x stores auth differently.
**Fix:** Primary check uses `claude auth status` CLI command (returns JSON with `loggedIn`), falls back to legacy credentials file.
**File:** `scripts/configure_providers.py`

---

## 5. Test Results

### Test 1: Chat with test agent (agent_fc56ba8c26f3)
**Input:** "can you trigger the social chat job?"
**Extraction result:** LLM returned 1 entity: "Agent" (the assistant itself)
**Problem:** Agent extracted as entity. Matched existing `entity_agent` junk.
**Status:** Self-exclusion fix applied (agent name now passed as exclusion). Prompt definition strengthened. Awaiting retest.

### Test 2: Job execution — Random Agent Social Chat
**Result after Step 1 fix:** Job executed successfully (no more `UnboundLocalError`)
**Extraction result:** 17 entities extracted — "social agent 1" (self) + 16 sibling agents from Matrix member list
**Dedup behavior per entity:**
- "social agent 1" → Stage 1 exact name match to existing `entity_social_agent_1` → UPDATED
- All 16 siblings → Stage 1 (no match) → Stage 2 semantic search (no match, threshold 0.7) → CREATED NEW
- Each entity properly got aliases (Matrix IDs like `@agent_xxx:localhost`)
- Each got familiarity=`known_of`
- Keywords attached where relevant (e.g., `matrix_chat` on Nova and Karrigan)

**Problems identified:**
1. "social agent 1" still extracted (self) — fixed by self-exclusion change
2. Generic "Agent" still extracted in chat context — fixed by prompt definition change
3. 16 entities from member list dump is noisy but arguably correct (they are real agents)

### Test 3: Background logging
**Result:** After `async_cleanup` fix, full `[SocialExtraction]` and `[SocialSummary]` logs now appear in agent log files. Complete visibility into:
- LLM input (conversation preview, exclusion list)
- Raw extraction output (all entities with types, keywords, aliases, familiarity)
- Post-filter results
- Dedup pipeline stages (Stage 1/2/3 per entity)
- Entity creation/update actions

---

## 6. What's Next

### Immediate (awaiting test results after restart)
- **Verify self-exclusion works** — agent should not extract itself ("social agent 1")
- **Verify concept rejection works** — generic terms like "Agent", "other agents" should not be extracted
- **Verify frontend** — familiarity badges and aliases visible in social network panel

### Short-term improvements to evaluate
- **Familiarity auto-upgrade:** When an entity that's `known_of` becomes the `user_id` in a real conversation, upgrade to `direct`. Currently familiarity is set only at extraction time.
- **Member list filtering:** Should all members of a Matrix room be extracted as entities, or only those actually discussed? This is a prompt question — "listed in a member roster" vs "discussed or interacted with" distinction.
- **Existing junk cleanup:** The 7 old entities (entity_agent, entity_other_agents, etc.) from before our changes still exist. Consider a cleanup script or manual deletion.

### Deferred (from original plan)
- EventNode / ConceptNode / full 4-tier storage
- Topic-to-person episode-mediated retrieval (Balog Model 2)
- Relationship strength as multi-dimensional vector (Granovetter dimensions)
- Fan effect / ACT-R retrieval scoring
- Periodic consolidation / forgetting curve

---

## 7. Files Modified (Complete List)

| File | Changes |
|------|---------|
| `schema/entity_schema.py` | Added aliases, familiarity, keywords; entity_type includes "group" |
| `schema/api_schema.py` | Added aliases, familiarity, keywords to API response model |
| `repository/social_network_repository.py` | New search_by_name_or_alias, aliases in keyword_search, field mapping |
| `module/social_network_module/social_network_module.py` | Parallelize post-hook, 3-stage dedup, search fix, self-exclusion |
| `module/social_network_module/_entity_updater.py` | DedupDecision, decide_merge_or_create, updated ExtractedEntity, logging |
| `module/social_network_module/_social_mcp_tools.py` | .tags→.keywords in merge |
| `module/social_network_module/prompts.py` | Rewritten extraction prompt, dedup prompt, keywords terminology |
| `module/social_network_module/test_persona.py` | .tags→.keywords |
| `module/job_module/_job_context_builder.py` | .tags→.keywords |
| `utils/database_table_management/create_instance_social_entities_table.py` | DDL for aliases, familiarity |
| `agent_runtime/logging_service.py` | async_cleanup() for proper log flush |
| `agent_runtime/agent_runtime.py` | Use async_cleanup in background task |
| `agent_runtime/_agent_runtime_steps/step_1_select_narrative.py` | Fix UnboundLocalError for forced narrative |
| `backend/routes/agents_social_network.py` | Return aliases, familiarity, keywords in API |
| `frontend/src/types/api.ts` | Added aliases, familiarity, keywords to TS interface |
| `frontend/src/components/awareness/EntityCard.tsx` | Familiarity badge, aliases display |
| `scripts/configure_providers.py` | Claude Code v2.x auth status check |
| `CLAUDE.md` | Added filename date convention for drafts |
