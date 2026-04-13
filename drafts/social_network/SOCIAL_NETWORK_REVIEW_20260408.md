# Social Network Module — Dataflow & Process Reference

**Date:** 2026-04-08 (updated 2026-04-09)
**Status:** Implementation complete — Phase 0-5 applied

## Changes Applied (2026-04-09)

### Design Principles (from cognitive science research)
- Social network stores **only social entities**: humans, agents, groups (things with agency)
- Non-social concepts (competitions, APIs, topics) are **keywords on people**, not entities
- `familiarity` field distinguishes `direct` (interacted with) from `known_of` (mentioned by others)
- `aliases` field enables cross-system ID matching (Matrix ID, platform agent ID, etc.)

### Schema Changes
- Added `aliases: List[str]` — cross-system IDs and alternate names
- Added `familiarity: str` — "direct" | "known_of"
- Added `entity_type: "group"` option (teams/squads with collective agency)
- Renamed Python field `tags` → `keywords` (DB column unchanged for backward compat)

### Extraction Prompt Rewrite
- Only extracts humans, agents, groups — rejects competitions, concepts, APIs, etc.
- Non-social context becomes keywords on the associated person
- Handles aliases: "alpha4 (@agent:localhost)" → ONE entity with alias
- Sets familiarity based on participation vs. mention

### 3-Stage Dedup Pipeline (post-hook)
```
Stage 1: Exact name+alias match (repo.search_by_name_or_alias)
  → 1 match → UPDATE directly
  → multiple → Stage 3
  → 0 → Stage 2

Stage 2: Vector similarity search (threshold=0.7, topK=3)
  → candidates found → Stage 3
  → no candidates → CREATE NEW

Stage 3: LLM merge decision (MERGE or CREATE_NEW)
  → MERGE → update existing entity (description, keywords, aliases)
  → CREATE_NEW → create new entity
```

### Search Fix
- Auto-detect now routes to `keyword_search` (name + description + keywords + aliases)
- Previously only searched tags column via JSON_SEARCH — names were unreachable

### Performance
- `asyncio.gather` parallelizes summary + extraction LLM calls
- Eliminated 3 redundant `repo.get_entity` calls in post-hook
- Fixed `should_update_persona` to check user input instead of agent output

---

---

## Module Files

| File | Role |
|------|------|
| `social_network_module.py` | Main module class, hooks, internal helpers |
| `_social_mcp_tools.py` | 9 MCP tools exposed to the agent |
| `_entity_updater.py` | LLM-powered update pipelines (summary, persona, batch extraction) |
| `prompts.py` | System instructions + LLM prompt templates |
| `social_network_repository.py` | MySQL data access layer |
| `entity_schema.py` | `SocialNetworkEntity` Pydantic model |
| `create_instance_social_entities_table.py` | DB table DDL |

---

## Data Model: `SocialNetworkEntity`

| Field | Type | Description |
|-------|------|-------------|
| `instance_id` | str | Module instance ID (data isolation key) |
| `entity_id` | str | Unique entity identifier |
| `entity_type` | str | `"user"` / `"agent"` |
| `entity_name` | str | Display name |
| `entity_description` | text | Cumulative natural-language description (managed ONLY by post-hook) |
| `identity_info` | JSON | Structured: organization, position, expertise |
| `contact_info` | JSON | Channels: matrix, slack, email, etc. |
| `tags` | JSON | Structured tags (code cap: 10, prompt says 3-5) |
| `expertise_domains` | JSON | Domain list |
| `related_job_ids` | JSON | Bidirectional index to Job module |
| `embedding` | JSON | 1536-dim OpenAI vector for semantic search |
| `persona` | text | Communication style guide (1-3 sentences) |
| `relationship_strength` | float | 0.0-1.0 (currently never updated beyond 0.0) |
| `interaction_count` | int | Conversation counter |
| `last_interaction_time` | datetime | Last interaction timestamp |
| `extra_data` | JSON | Extension fields |

---

## Dataflow: Conversation Lifecycle

### Phase 1 — Pre-Conversation: `hook_data_gathering(ctx_data)`

Called during the orchestrator's Step 2 (data gathering), before the agent sees user input.

```
hook_data_gathering(ctx_data)
│
├─ 1. Get instance_id
│     └─ self.instance_id cached? → use it
│     └─ else: InstanceRepository.get_by_agent(agent_id, "SocialNetworkModule")
│              → cache first result into self.instance_id
│     └─ if None: set ctx_data.social_network_current_entity = "first time" message, return
│
├─ 2. Load current user entity
│     ├─ 2a. Exact match: repo.get_entity(entity_id=ctx_data.user_id, instance_id)
│     │
│     └─ 2b. Fallback: _fuzzy_find_entity(ctx_data, instance_id)
│           ├─ Extract sender_name from ctx_data.extra_data["channel_tag"]
│           ├─ repo.keyword_search(instance_id, sender_name, limit=3)
│           └─ Pick entity with highest interaction_count
│
├─ 3. If entity found:
│     ├─ Format display_text (name, description, tags, interaction_count, last_interaction_time)
│     ├─ Append persona block if entity.persona exists
│     ├─ Set ctx_data.social_network_current_entity = display_text
│     └─ If entity.related_job_ids:
│           Write to ctx_data.extra_data["related_job_ids"] (for JobModule to read)
│           Write to ctx_data.extra_data["current_entity_id"]
│           Write to ctx_data.extra_data["current_entity_name"]
│
├─ 4. If entity NOT found:
│     └─ Set ctx_data.social_network_current_entity = "first time meeting" message
│
└─ 5. Load known agent entities
      ├─ repo.get_all_entities(instance_id, entity_type="agent", limit=50)
      └─ Write list to ctx_data.extra_data["known_agent_entities"]
          (used by MatrixModule and others)
```

**Output:** `ctx_data.social_network_current_entity` is injected into the agent's system prompt via the `{social_network_current_entity}` placeholder in `prompts.py`.

---

### Phase 2 — During Conversation: MCP Tools (agent-initiated)

The agent can call these tools during its reasoning loop. All tools go through the same entry pattern:

```
MCP tool call
│
└─ _get_instance_and_module(agent_id)
   ├─ get_db_client_fn() → new DB connection
   ├─ InstanceRepository.get_by_agent(agent_id, "SocialNetworkModule") → instance_id
   └─ Create temp SocialNetworkModule(agent_id, db, instance_id)
      └─ Delegates to module method
```

#### Tool 1: `extract_entity_info(agent_id, entity_id, updates, update_mode="merge")`
**Purpose:** Store/update structured entity info (name, tags, contact_info, identity_info).

```
extract_entity_info
│
├─ Parse updates (JSON string → dict if needed)
├─ repo.get_entity(entity_id, instance_id)
│
├─ If entity EXISTS and mode="merge":
│     ├─ Merge identity_info: {**existing, **new}
│     ├─ Merge contact_info: merge_contact_info() (deep merge + normalize)
│     ├─ Merge tags: case-insensitive dedup, cap at 10
│     ├─ REJECT entity_description updates (log warning, pop from dict)
│     └─ repo.update_entity_info(entity_id, instance_id, merged_updates)
│
└─ If entity NOT EXISTS:
      ├─ Pop entity_type, entity_name, identity_info, contact_info, tags from updates
      ├─ normalize_contact_info(raw_contact)
      ├─ Set entity_description = "" (empty, awaiting hook)
      └─ repo.add_entity(...)
```

#### Tool 2: `search_social_network(agent_id, search_keyword, search_type="auto", top_k=5)`
**Purpose:** Find entities by ID, tags, or semantic similarity.

```
search_social_network
│
├─ search_type="auto" detection:
│     ├─ keyword.startswith("user_", "entity_") → "exact_id"
│     └─ else → "tags"
│
├─ exact_id: repo.get_entity(keyword, instance_id) → single result
│
├─ tags: repo.search_by_tags(instance_id, keyword)
│     └─ SQL: JSON_SEARCH(tags, 'one', '%keyword%') IS NOT NULL
│        ORDER BY relationship_strength DESC
│
└─ semantic: get_embedding(keyword) → repo.semantic_search(instance_id, embedding)
      └─ Loads ALL entities from DB
         Computes cosine_similarity in Python for each
         Filters by min_similarity=0.3
         Sorts descending, returns top limit
```

#### Tool 3: `get_contact_info(agent_id, entity_id)`
```
get_contact_info → recall_entity_info → _load_entity_info → repo.get_entity
Returns: entity_name + contact_info dict
```

#### Tool 4: `get_agent_social_stats(agent_id, sort_by, top_k, filter_tags)`
```
get_agent_social_stats
│
├─ repo.get_all_entities(instance_id, limit=1000)
├─ Filter by tags (Python-side if filter_tags provided)
├─ Sort in Python:
│     ├─ "recent"   → by last_interaction_time DESC
│     ├─ "frequent" → by interaction_count DESC
│     └─ "strong"   → by relationship_strength DESC
└─ Slice [:top_k]
   Return: entity_id, entity_name, entity_description, last_interaction_time
```

#### Tool 5: `check_channel_updates(agent_id, channels="")`
```
check_channel_updates
│
├─ ChannelSenderRegistry.available_channels() → list
├─ Filter by requested channels
└─ For each channel:
      └─ "matrix": MatrixCredentialManager → NexusMatrixClient.list_rooms()
         Returns: rooms_count, matrix_user_id
```

#### Tool 6: `contact_agent(agent_id, target_entity_id, message, channel, room_id)`
```
contact_agent
│
├─ _load_entity_info(target_entity_id) → get entity contact_info
├─ Channel selection:
│     ├─ Explicit channel param → use it
│     ├─ get_preferred_channel(contact_info) → preferred
│     ├─ Auto-detect: first available channel with info for entity
│     └─ Last resort: first registered channel
├─ ChannelSenderRegistry.get_sender(channel) → sender function
├─ Resolve target_channel_id from contact_info
├─ Resolve room_id from contact_info if not provided
└─ sender(agent_id, target_id, message, room_id)
```

#### Tool 7: `merge_entities(agent_id, source_entity_id, target_entity_id, keep_target_name)`
```
merge_entities
│
├─ Fetch source + target entities
├─ Merge:
│     ├─ tags: union, case-insensitive dedup, cap 10
│     ├─ identity_info: {**source, **target} (target precedence)
│     ├─ contact_info: merge_contact_info(source, target)
│     ├─ related_job_ids: set union
│     ├─ entity_description: target + "\n(Merged from source): " + source
│     ├─ interaction_count: sum
│     └─ last_interaction_time: max
├─ repo.update_entity_info(target)
└─ repo.delete_entity(source)
```

#### Tool 8: `delete_entity(agent_id, entity_id)`
```
delete_entity → repo.get_entity (verify exists) → repo.delete_entity
```

#### Tool 9: `create_agent(agent_id, agent_name, awareness, agent_description)`
```
create_agent
│
├─ AgentRepository.get_agent(agent_id) → get owner user_id
├─ Generate new_agent_id = "agent_{uuid[:12]}"
├─ AgentRepository.add_agent(...)
├─ Create workspace dir + Bootstrap.md
├─ Create AwarenessModule instance + set awareness text
└─ ensure_agent_registered() on NexusMatrix (non-fatal)
```

---

### Phase 3 — Post-Conversation: `hook_after_event_execution(params)`

Called during the orchestrator's Step 7 (post-execution), after agent response is finalized.

```
hook_after_event_execution(params)
│
├─ 1. Get instance_id (same lazy pattern as hook_data_gathering)
│
├─ 2. Get user_id from params.user_id → fallback self.user_id
│     └─ If no user_id: return early
│
├─ 3. Check entity exists
│     ├─ repo.get_entity(user_id, instance_id)
│     └─ If not exists: repo.add_entity(minimal: entity_id=user_id, type="user", name=user_id)
│
├─ 4. Summarize conversation (LLM call #1)
│     └─ summarize_new_entity_info(input_content, final_output)
│        ├─ OpenAIAgentsSDK().llm_function(ENTITY_SUMMARY_INSTRUCTIONS, ...)
│        └─ Returns: one-line summary or ""
│     └─ If empty: update_interaction_stats() → return early
│
├─ 5. Append to entity_description
│     └─ append_to_entity_description(repo, user_id, instance_id, new_summary)
│        ├─ Fetch entity, get existing description
│        ├─ Append: "{existing}\n- {new_info}"
│        ├─ If len > 2000: compress_description(long_description) (LLM call — conditional)
│        │     └─ OpenAIAgentsSDK().llm_function(DESCRIPTION_COMPRESSION_INSTRUCTIONS, ...)
│        └─ repo.update_entity_info(entity_id, updates={"entity_description": ...})
│
├─ 6. Update embedding
│     └─ update_entity_embedding(repo, user_id, instance_id)
│        ├─ Fetch entity (DB call)
│        ├─ Build text: "Name: ... \n Description: ... \n Tags: ..."
│        ├─ get_embedding(text) → 1536-dim vector
│        ├─ repo.update_entity_info(updates={"embedding": vector})
│        └─ store_embedding("entity", entity_id, embedding) → dual-write to embedding_store
│
├─ 7. Update interaction stats
│     └─ update_interaction_stats(repo, user_id, instance_id)
│        └─ SQL: SET interaction_count = interaction_count + 1, last_interaction_time = NOW()
│
├─ 8. Persona update (conditional — LLM call #2)
│     ├─ Fetch entity again (DB call)
│     ├─ should_update_persona(entity, final_output):
│     │     ├─ persona is None → True
│     │     ├─ interaction_count % 10 == 0 → True
│     │     └─ change signals in final_output → True
│     │
│     └─ If True: infer_persona(entity, awareness, job_info, recent_conversation)
│           ├─ OpenAIAgentsSDK().llm_function(PERSONA_INFERENCE_INSTRUCTIONS, ...)
│           └─ update_entity_persona(repo, user_id, instance_id, new_persona)
│
└─ 9. Batch entity extraction (LLM call #3)
      ├─ Fetch entity again (DB call) → get primary_name
      ├─ extract_mentioned_entities(input_content, final_output, primary_name)
      │     └─ OpenAIAgentsSDK().llm_function(BATCH_ENTITY_EXTRACTION_INSTRUCTIONS, ...)
      │        Returns: List[ExtractedEntity(name, type, summary, tags)]
      │
      └─ For each mentioned entity:
            ├─ Generate candidate ID: "entity_{name.lower().replace(' ', '_')}"
            ├─ repo.get_entity(candidate_id, instance_id)
            │
            ├─ If not found: fuzzy fallback
            │     ├─ repo.keyword_search(instance_id, name, limit=3)
            │     └─ Pick match with highest interaction_count
            │
            ├─ If matched (existing):
            │     ├─ append_to_entity_description(matched_id, summary)
            │     └─ Merge tags (case-insensitive dedup, cap 10)
            │          repo.update_entity_info(matched_id, {"tags": merged})
            │
            └─ If no match:
                  └─ repo.add_entity(candidate_id, type, name, summary, tags)
```

---

## Instruction Injection Flow

```
Module init:
  self.instructions = SOCIAL_NETWORK_MODULE_INSTRUCTIONS.replace("{agent_id}", agent_id)

get_instructions(ctx_data):
  → instructions.replace("{social_network_current_entity}", ctx_data.social_network_current_entity)

Final system prompt includes:
  - Module purpose
  - Entity memory rules (what/when/how to record)
  - Tool usage guidance (9 tools with trigger scenarios)
  - Tagging rules (expertise levels, roles, intent, sales stages)
  - Current user entity info (injected by hook_data_gathering)
  - Behavior expectations
```

---

## Repository: Search Methods

| Method | SQL Strategy | Searches In | Sorted By |
|--------|-------------|-------------|-----------|
| `get_entity` | `WHERE entity_id = %s AND instance_id = %s` | exact entity_id | N/A |
| `get_all_entities` | `WHERE instance_id = %s [AND entity_type = %s]` | all entities | `updated_at DESC` |
| `search_by_tags` | `JSON_SEARCH(tags, 'one', '%keyword%')` | tags JSON column | `relationship_strength DESC` |
| `keyword_search` | `LIKE %keyword%` | entity_name, entity_description, tags | `interaction_count DESC, updated_at DESC` |
| `semantic_search` | `SELECT * WHERE instance_id = %s` + Python cosine | embedding vectors | cosine similarity DESC |

---

## Cross-Module Integration

| Direction | What | How |
|-----------|------|-----|
| SocialNetwork → Context | Current entity info | `ctx_data.social_network_current_entity` |
| SocialNetwork → JobModule | Related job IDs | `ctx_data.extra_data["related_job_ids"]` |
| SocialNetwork → MatrixModule | Known agent entities | `ctx_data.extra_data["known_agent_entities"]` |
| SocialNetwork → EmbeddingStore | Entity embeddings | `store_embedding()` dual-write |
| MatrixModule → SocialNetwork | Channel/sender info | `ctx_data.extra_data["channel_tag"]` |
| Agent → SocialNetwork | Entity CRUD | MCP tools (port 7802) |

---

## LLM Calls Summary

| Function | Prompt | Output Schema | When Called |
|----------|--------|---------------|------------|
| `summarize_new_entity_info` | `ENTITY_SUMMARY_INSTRUCTIONS` | `SummaryOutput(summary: str)` | Every post-hook |
| `compress_description` | `DESCRIPTION_COMPRESSION_INSTRUCTIONS` | `CompressedDescriptionOutput(compressed_summary: str)` | When description > 2000 chars |
| `infer_persona` | `PERSONA_INFERENCE_INSTRUCTIONS` | `PersonaOutput(persona: str)` | First interaction, every 10th, change signals |
| `extract_mentioned_entities` | `BATCH_ENTITY_EXTRACTION_INSTRUCTIONS` | `BatchExtractionOutput(entities: List[ExtractedEntity])` | Every post-hook |

All use `OpenAIAgentsSDK().llm_function()` — new SDK instance per call.
