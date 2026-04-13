# Narrative-EverMemOS Decoupling — Design Document

> Status: In progress
> Last updated: 2026-04-07

---

## 1. Current Final Prompt Structure (What Claude sees)

The final `messages` list sent to Claude Agent SDK is built in two stages:

### Stage 1: System Prompt (`build_complete_system_prompt`)
Built by joining these parts with `\n\n`:

```
Part 1: Narrative Info (main narrative)
  └── Source: narrative_service.combine_main_narrative_prompt(main_narrative)
  └── Contains: narrative_id, type, name, description, current_summary, actors
  └── EverMemOS coupling: NONE (pure narrative data)

Part 2: Event History
  └── DISABLED (commented out since 2025-12-10)

Part 3: Auxiliary Narratives
  └── Source: narrative_list[1:] + evermemos_memories
  └── Contains: name, topic_hint/summary, event_count, + episode_summaries (3 × 150 chars)
  └── EverMemOS coupling: YES — episode_summaries from evermemos_memories cache

Part 4: Module Instructions
  └── Source: each active module's get_instructions(ctx_data)
  └── Contains: ChatModule rules, BasicInfo (agent identity, world view), 
      Awareness, SocialNetwork, JobModule, MatrixModule, SkillModule, RAGModule
  └── EverMemOS coupling: INDIRECT — ChatModule's get_instructions doesn't use EverMemOS,
      but its hook_data_gathering does (loads episode_contents as long-term memory)

Part 5: Bootstrap Injection (first 3 turns only)
  └── Source: Bootstrap.md file
  └── EverMemOS coupling: NONE
```

### Stage 2: Final Assembly (`build_input_for_framework`)
Takes system prompt + chat history and builds the messages list:

```
final_messages = [
  {role: "system", content: system_prompt + short_term_memory_section},
  {role: "user/assistant", content: long_term_msg_1},    ← from ChatModule or EverMemOS
  {role: "user/assistant", content: long_term_msg_2},    ← from ChatModule or EverMemOS  
  ... (up to 40 messages)
  {role: "user", content: current_user_input}
]
```

**What ends up in the final prompt (current vs proposed):**

Note: "Long-term memory" and "Chat History" are the SAME data. ChatModule loads messages tagged as `long_term`, these become conversation messages in `build_input_for_framework()`, then Claude SDK reformats them as `=== Chat History ===` and appends to the system prompt. Not loaded twice.

| Prompt section | Current source | Current max | EverMemOS coupled? | Proposed source | Proposed max | Change needed |
|---|---|---|---|---|---|---|
| **Narrative Info** | MySQL narrative: name, description, summary, actors, keywords | No limit (summary unbounded) | No | Same but with bounded summary | ~2000 tokens (8000 chars) | Add summary compaction |
| **Auxiliary Narratives** | MySQL narrative summaries + evermemos episode_summaries as "Related Content" | 3 narratives × (name + summary + 3×150 char episodes) | **Yes** — episode_summaries | **Remove** — replaced by Relevant Memory section | — | Remove section entirely |
| **Relevant Memory** | **Does not exist** | — | — | **NEW**: EverMemOS episodes retrieved by query, flat list, not grouped by narrative | ~3000 tokens (12000 chars) | New section |
| **Module Instructions** | Each module's `get_instructions(ctx_data)` | No limit per module, no total limit | No | Same but each module self-limits | ~8000 tokens total | Add per-module budgets |
| **Short-term Memory** | ChatModule DB: 15 most recent messages from other narratives | 40000 chars (~10k tokens) | No | Same | ~2000 tokens (8000 chars) | Reduce budget, add relevance filter (future) |
| **Bootstrap** | Bootstrap.md file | Fixed template | No | Same | ~1000 tokens | No change |
| **Conversation History Part A** (recent messages) | Continuity path: ChatModule DB (40 msgs). Non-continuous: EverMemOS episode_contents — switches source depending on path | 40 messages + Claude SDK caps at 30000 chars | **Yes** — non-continuous path uses EverMemOS | **Always** ChatModule DB: last 30 messages from selected narrative, same path regardless of continuous/non-continuous | TBD | Stop using EverMemOS; always ChatModule DB; reduce from 40 to 30 msgs |
| **Conversation History Part B** (relevant old messages) | **Does not exist** | — | — | **NEW**: Embedding retrieval within same narrative's ChatModule messages. Finds older relevant messages beyond the 30-message window. Uses our own embeddings (not EverMemOS). Top_k retrieved, deduped against Part A. | TBD | New capability. Requires `chat_message_embeddings` table + embed on write in ChatModule hook |
| **Dedup** | No dedup between EverMemOS episodes and ChatModule short-term | — | — | **NEW**: Dedup across Part A, Part B, and Short-term memory. Part A vs B by message_index. Short-term vs A/B by content_hash. EverMemOS episodes (Relevant Memory) are different format — no dedup needed. | — | New logic in context assembly |

### Stage 3: Claude Agent SDK (`agent_loop`)
Additional processing on top:

```
system_prompt = all system messages joined
conversation_history = user/assistant messages formatted as "User: ... \n Assistant: ..."
  └── appended to system_prompt as "=== Chat History ==="  
  └── truncated to MAX_HISTORY_LENGTH = 30000 chars
this_turn_user_message = last user message (sent as query to Claude)

Total system_prompt capped at MAX_SYSTEM_PROMPT_LENGTH = 60000 chars
```

---

## 2. EverMemOS Coupling Points (What to decouple)

### Write path (Step 5 background hooks):
| Location | What it does | After decoupling |
|---|---|---|
| `memory_module.py:258` | Writes event to EverMemOS with `group_id = narrative_id` | Change to `group_id = agent_id` |

### Read path — Narrative Selection (Step 1):
| Location | What it does | After decoupling |
|---|---|---|
| `narrative_service.py:237` | Calls `retrieve_top_k()` which calls EverMemOS search | **Remove** — narrative selection uses own data only |
| `retrieval.py:232` | `_search()` calls EverMemOS, returns episodes grouped by narrative | **Remove from narrative selection** |
| `retrieval.py:280-290` | Builds `evermemos_memories` cache from search results | **Remove** — no longer built during selection |
| `retrieval.py:323` | `_llm_unified_match()` uses episode_summaries as matched_content | **Remove** — LLM judge uses narrative data only |

### Read path — Context Building (ContextRuntime):
| Location | What it does | After decoupling |
|---|---|---|
| `context_runtime.py:126-128` | Injects `evermemos_memories` into ctx_data | **Replace** — call EverMemOS directly here, independent of narrative selection |
| `context_runtime.py:400` | Uses evermemos_memories for auxiliary narrative "Related Content" | **Replace** — fetch episodes by query relevance, not by narrative_id |
| `chat_module.py:259-293` | Loads episode_contents as long-term memory from evermemos_memories | **Replace** — call EverMemOS search directly with query, not from cache |

---

## 3. Target State — Three Independent Layers

### Layer 1: Narrative Selection (uses ONLY narrative data)
```
Input: query, agent_id, user_id, session
Process:
  - Continuity detection (using narrative metadata + session)
  - If not continuous: search by routing_embedding/centroid (VectorStore)
  - LLM judge using narrative name + summary + keywords (NO episodes)
Output: selected narrative_id(s)
```

### Layer 2: Memory Retrieval (EverMemOS — pure memory provider)
```
Input: query_text, agent_id, user_id, top_k
Process:
  - RRF search (BM25 + vector) within agent's episodes
  - group_id = agent_id (not narrative_id)
Output: List[Episode] ranked by relevance (text + score)
Does NOT know about narratives
```

### Layer 3: Context Assembly (budget-controlled prompt builder)
```
Input: 
  - narrative metadata (from Layer 1)
  - relevant episodes (from Layer 2)
  - conversation history (from ChatModule DB)
  - short-term memory (from ChatModule DB)
  - module instructions (from each module)
Process: 
  - Allocate token budgets per section
  - Each source renders within its budget
  - Assemble final messages list
Output: messages list for Claude Agent SDK
```

---

## 4. Target Prompt Structure (After Decoupling)

```
System Prompt:
  [Part 1] Narrative Metadata                    ← from Layer 1 (narrative own data)
           name, summary, actors, keywords
           Budget: ~2000 tokens
           
  [Part 2] Relevant Memory (EverMemOS episodes)  ← NEW: from Layer 2 (independent retrieval)
           Top-K episodes ranked by query relevance
           Not grouped by narrative — flat list
           Budget: ~3000 tokens

  [Part 3] Module Instructions                    ← from each active module
           ChatModule, BasicInfo, Awareness, SocialNetwork, Job, Matrix, Skill, RAG
           Budget: ~8000 tokens

  [Part 4] Short-term Memory                      ← from ChatModule DB (other narratives)
           Recent messages from other topics
           Budget: ~2000 tokens

  [Part 5] Bootstrap (if applicable)              ← from file
           Budget: fixed

Conversation Messages:
  [Long-term history]                             ← from ChatModule DB (current narrative)
           Recent 20 rounds of exact messages
           Budget: ~5000 tokens

  [Current user message]
           Budget: unlimited

Total budget: ~20000 tokens (system) + ~5000 tokens (history) = ~25000 tokens
Leaving ~75000 tokens for agent reasoning + tool output (on 100K context)
```

**Key changes from current:**
1. Relevant Memory is a NEW dedicated section — episodes retrieved by query, not by narrative
2. Auxiliary Narratives section REMOVED — replaced by Relevant Memory
3. Long-term memory ALWAYS from ChatModule DB — no more EverMemOS/ChatModule fallback switching
4. Each section has a defined budget

---

## 5. EverMemOS Client — Current vs New Contract

### Current Client: `EverMemOSClient`

**Initialization:** One client per (agent_id, user_id), cached globally.

| Method | Signature | What it does | Coupled to Narrative? |
|---|---|---|---|
| `__init__` | `(agent_id, user_id, base_url)` | Create client, set up API URLs | No |
| `write_event` | `(event, narrative) → bool` | 1. `_ensure_conversation_meta(narrative)` — POST /conversation-meta with group_id=narrative_id, tags=["narrative", agent_id] | **Yes** — uses narrative_id as group_id, narrative name/description in metadata |
| `_ensure_conversation_meta` | `(narrative) → bool` | POST /conversation-meta — creates group metadata per narrative_id | **Yes** — group_id=narrative_id |
| `_event_to_messages` | `(event, narrative) → List[Dict]` | Converts event to 2 messages (user + agent). group_id=narrative_id, sender=user_id | **Yes** — group_id=narrative_id, group_name=narrative name |
| `search_narratives` | `(query, top_k, agent_narrative_ids) → List[NarrativeSearchResult]` | 1. GET /memories/search with user_id, top_k×3. 2. Post-filter by agent_narrative_ids. 3. Aggregate by narrative_id. | **Yes** — searches by user_id, aggregates by group_id=narrative_id, returns per-narrative scores |
| `_filter_pending_messages_by_agent` | `(pending_messages, agent_narrative_ids) → set` | Filter pending messages' group_ids against agent's narrative_ids | **Yes** — uses narrative_ids as filter |
| `_aggregate_by_narrative` | `(raw_memories, raw_scores, top_k, ...) → List[NarrativeSearchResult]` | Groups episodes by narrative_id, takes max score per narrative, extracts summaries/contents, scales RRF scores | **Yes** — entire purpose is narrative-level aggregation |

**Summary:** 5 out of 7 methods are coupled to narrative_id as group_id.

### Current data flow:

```
Write:
  event + narrative → _event_to_messages() → group_id = narrative_id
                    → _ensure_conversation_meta() → group_id = narrative_id
                    → POST /memories

Search:  
  query + user_id → GET /memories/search (user_id filter, no agent_id)
                  → raw results (episodes grouped by group_id = narrative_id)
                  → _filter_pending_messages_by_agent (agent_narrative_ids)
                  → _aggregate_by_narrative (max score per narrative, extract summaries)
                  → List[NarrativeSearchResult] (narrative_id, score, episode_summaries, episode_contents)
```

### Proposed New Client

**Key change:** `group_id` = `agent_id` instead of `narrative_id`. EverMemOS groups all episodes for an agent together, not per narrative.

| Method | Current | Proposed | Change |
|---|---|---|---|
| `__init__` | `(agent_id, user_id)` | Same | No change |
| `write_event` | `(event, narrative)` → uses narrative_id/name | `(event, agent_id)` → uses agent_id. No narrative dependency. | Remove narrative param. group_id=agent_id. Simplify conversation_meta. |
| `_ensure_conversation_meta` | Per narrative_id | Per agent_id (called once, not per narrative) | Simplify — one meta per agent, not per narrative |
| `_event_to_messages` | group_id=narrative_id, group_name=narrative name | group_id=agent_id, group_name=agent_name or agent_id | Remove narrative dependency |
| `search_narratives` | Returns per-narrative scores + episode_summaries + episode_contents | **Replace with `search_episodes`**: returns flat list of episodes, no narrative grouping | New method, completely different return type |
| `_filter_pending_messages_by_agent` | Filters by agent_narrative_ids | **Remove** — group_id=agent_id means search is already agent-scoped | Delete method |
| `_aggregate_by_narrative` | Groups by narrative_id, max score, RRF scaling | **Remove** — no narrative aggregation needed | Delete method |

### Proposed new method signatures:

```python
class EverMemOSClient:
    def __init__(self, agent_id: str, user_id: str, base_url: Optional[str] = None):
        # Same as current
    
    async def write_event(self, event: "Event") -> bool:
        """
        Write event messages to EverMemOS.
        group_id = self.agent_id (not narrative_id)
        No narrative param needed.
        """
    
    async def search_episodes(
        self,
        query: str,
        top_k: int = 20,
    ) -> List[EpisodeResult]:
        """
        Pure memory retrieval — flat list of relevant episodes.
        Searches within group_id = self.agent_id.
        No narrative grouping. No aggregation. No top_k * 3 hack.
        """
        params = {
            "query": query,
            "top_k": top_k,
            "memory_types": "episodic_memory",
            "retrieve_method": "rrf",
            "group_id": self.agent_id,   # agent isolation built-in
            "user_id": self.user_id,
        }
        # Parse response into flat list of EpisodeResult
        # No _aggregate_by_narrative needed

@dataclass
class EpisodeResult:
    episode_text: str       # full episode content  
    summary: str            # episode summary
    score: float            # raw RRF score (no ×10 scaling needed — not compared with vector scores)
    timestamp: str          # episode creation time
```

### What gets removed:

| Code | Location | Why removed |
|---|---|---|
| `_aggregate_by_narrative()` | client.py:424-666 | No narrative grouping needed |
| `_filter_pending_messages_by_agent()` | client.py:364-422 | group_id=agent_id handles agent isolation |
| `search_narratives()` | client.py:282-362 | Replaced by `search_episodes()` |
| `top_k * 3` hack | client.py:306 | No cross-agent pollution with group_id=agent_id |
| RRF score ×10 scaling | client.py:607-620 | Scores are for episode ranking only, no need to match vector thresholds |
| `evermemos_memories` cache building | retrieval.py:277-290, 682-708 | No longer built during narrative selection |

### Migration consideration:

Existing EverMemOS data has `group_id = narrative_id`. After change, new writes use `group_id = agent_id`. Options:
- **Let old data age out** — new episodes accumulate under agent_id, old ones under narrative_ids won't be found by new search (group_id filter mismatches). Over time, new data dominates.
- **Backfill** — script to update all existing episodes' group_id from narrative_id to agent_id. Requires EverMemOS API or direct MongoDB update.
- **Dual search** (transition period) — search both group_id=agent_id AND group_id=any of agent's narrative_ids. Merge results. Remove after backfill complete.

---

## 6. Narrative Selection — Current vs Decoupled

### Current `NarrativeService.select()` flow:

```
Step 1: Continuity detection (LLM)
  → uses: session.last_query, narrative metadata
  → EverMemOS: NOT involved ✓

Step 2: Generate query embedding
  → uses: get_embedding(input_content)
  → EverMemOS: NOT involved ✓

Step 3a (continuous path):
  → main narrative = session.current_narrative_id
  → auxiliary search via retrieve_top_k_by_embedding() → VectorStore only
  → EverMemOS: NOT involved ✓ (query_text not passed)

Step 3b (non-continuous path):
  → calls retrieve_top_k() which calls _search():
    → _search() tries EverMemOS first (if enabled + query_text provided)     ← COUPLED
    → falls back to VectorStore if EverMemOS empty/disabled
  → builds evermemos_memories cache from search results                       ← COUPLED
  → LLM judge uses episode_summaries as matched_content                      ← COUPLED
  → returns evermemos_memories in NarrativeSelectionResult                    ← COUPLED

Step 4: Update session
  → EverMemOS: NOT involved ✓
```

**Coupling is only in Step 3b (non-continuous path).** The continuous path is already decoupled.

### What needs to change for decoupling:

| Current (Step 3b) | After decoupling |
|---|---|
| `_search()` tries EverMemOS → aggregates episodes by narrative → returns scored narratives | `_search()` uses VectorStore only. No EverMemOS call. |
| `_llm_unified_match()` includes episode_summaries as matched_content | LLM judge uses narrative metadata only (name, summary, keywords). No episodes. |
| `retrieve_top_k()` builds evermemos_memories cache | No evermemos_memories. Return only narrative selection result. |
| `NarrativeSelectionResult.evermemos_memories` passed to ContextRuntime | Remove this field. EverMemOS called independently in context assembly. |
| `_aggregate_by_narrative()` groups episodes, scales RRF scores | Not needed. Remove. |
| `_filter_pending_messages_by_agent()` | Not needed. Remove. |
| `_enhance_with_events()` blends event embeddings into scores | Keep or replace with centroid blending. VectorStore-only concern. |

### After decoupling — what narrative selection uses:

| Signal | Source | Already works without EverMemOS? |
|---|---|---|
| Continuity detection (LLM) | Session + narrative metadata | Yes ✓ |
| VectorStore cosine search | routing_embedding (narrative-level) | Yes ✓ |
| Narrative centroid | narrative_centroids table | Yes ✓ (our new addition) |
| LLM judge candidates | Narrative name, summary, keywords | Yes ✓ (just remove episode_summaries from prompt) |
| Participant narratives | MySQL (narrative actors) | Yes ✓ |
| Default narratives | MySQL (8 built-in) | Yes ✓ |

**Key insight: narrative selection ALREADY works without EverMemOS on every path except the non-continuous `retrieve_top_k()`.** The decoupling is removing EverMemOS from that one path and letting VectorStore (+ centroid) handle it alone.

---

## 6. New Narrative Selection Contract

```python
async def select(
    agent_id: str,
    user_id: str,
    input_content: str,
    session: Optional[ConversationSession] = None,
    awareness: Optional[str] = None,
) -> NarrativeSelectionResult:
    """
    Select narrative using ONLY narrative's own data.
    No EverMemOS involvement.
    """
    # 1. Continuity detection (same as before)
    # 2. If not continuous:
    #    - Search by routing_embedding / centroid (VectorStore)
    #    - LLM judge using: narrative name, summary, keywords
    #    - NO episode_summaries, NO matched_content from EverMemOS
    # 3. Return selected narrative_id(s)
```

---

## 7. New Context Assembly

Since narrative selection and EverMemOS are decoupled, they can run **in parallel** before context assembly:

```
User message arrives
    │
    ├── async Task 1: Narrative Selection (VectorStore, LLM judge)
    │     → returns: selected narrative_id(s)
    │
    ├── async Task 2: EverMemOS Episode Search (RRF search)
    │     → returns: List[EpisodeResult] (top 20 relevant episodes)
    │
    ▼ (await both)
    
Context Assembly
    │
    ├── Load narrative metadata (from Task 1 result)
    ├── Load conversation history Part A: last 30 messages from ChatModule DB (from Task 1 narrative_id)
    ├── Load conversation history Part B: embedding retrieval from ChatModule (from Task 1 narrative_id)
    ├── Load short-term memory: recent 15 messages from other narratives (ChatModule DB)
    ├── Dedup: Part A ∩ Part B ∩ short-term memory
    ├── Relevant Memory: episodes from Task 2 result
    ├── Module instructions: from each active module
    ├── Bootstrap (if applicable)
    │
    ▼
    Final messages list for Claude Agent SDK
```

**Key: Narrative Selection and EverMemOS Search run concurrently.** They don't depend on each other. This saves latency — currently they run sequentially (EverMemOS called inside narrative selection).

**Where to implement this parallel execution:**
- Currently `step_3_agent_loop.py` calls `ContextRuntime.run()` which does everything sequentially
- After decoupling: `ContextRuntime.run()` uses `asyncio.gather()` for narrative selection + EverMemOS search
- Or: move the parallel dispatch up to `step_1_select_narrative.py` / `step_3_agent_loop.py`

**Context assembly does NOT have budget control yet.** Budget allocation is a separate concern to address after decoupling. For now, each section loads its full content. Budget limits will be added as a follow-up.

---

## 8. Implementation Order

| Step | What | Changes | Depends on |
|---|---|---|---|
| **A** | Define EpisodeResult dataclass and new client method signature | New file or add to models | Nothing |
| **B** | Change EverMemOS write: group_id = agent_id | `client.py` write path | Nothing |
| **C** | Add `search_episodes()` to client (flat results, no narrative grouping) | `client.py` new method | A |
| **D** | Remove EverMemOS from narrative selection | `narrative_service.py`, `retrieval.py` | Nothing |
| **E** | Add parallel EverMemOS call in ContextRuntime (independent of narrative selection) | `context_runtime.py` | C |
| **F** | Update context assembly to use new sources (episodes, Part A, Part B, dedup) | Refactor `context_runtime.py` + `chat_module.py` | D, E |
| **G** | Remove old coupling code | `evermemos_memories` passing, old aggregation, `search_narratives()` | D, E, F |
| **H** | Add Part B: embed chat messages on write + retrieval on read | `chat_module.py` + `embeddings_store` or `events` table pattern | F |

A, B, D can be done in parallel. C depends on A. E depends on C. F depends on D+E. G is cleanup. H can be done independently.

---

## 9. Existing Embedding Infrastructure (for Part B chat message retrieval)

### When EverMemOS is disabled — current fallback:

| Component | EverMemOS enabled | EverMemOS disabled |
|---|---|---|
| **Narrative selection** | EverMemOS RRF search → fallback to VectorStore | VectorStore only (cosine on routing_embedding) |
| **Long-term memory** | episode_contents from evermemos_memories | ChatModule DB raw messages (last 40) |
| **Memory write** | Writes to EverMemOS | Skipped entirely |

### `embeddings_store` table (NEW infrastructure, already exists):

```sql
embeddings_store:
  entity_type  VARCHAR(32)  -- "narrative" / "event" / "job" / "entity"
  entity_id    VARCHAR(64)  -- e.g. "nar_xxx", "job_xxx"
  model        VARCHAR(128) -- e.g. "text-embedding-3-small"
  dimensions   INT          -- 1536
  vector       JSON         -- [0.01, 0.02, ...]
  source_text  TEXT         -- original text for re-embedding
  UNIQUE KEY (entity_type, entity_id, model)
```

### `embedding_store_bridge.py` (helpers):
- `store_embedding(entity_type, entity_id, vector, source_text)` — upsert
- `get_stored_embedding(entity_type, entity_id)` — single lookup
- `get_stored_embeddings_batch(entity_type, entity_ids)` — batch lookup
- `get_all_stored_embeddings(entity_type)` — all embeddings of a type
- Model-aware: automatically uses the current configured embedding model

### How to use for Part B (chat message embedding retrieval):

**Option: Reuse `embeddings_store` with `entity_type = "chat_message"`**
- `entity_id` = `"{instance_id}_{message_index}"` (unique per message)
- Write: in ChatModule `hook_after_event_execution`, after saving message pair, embed and store
- Read: in ChatModule `hook_data_gathering`, embed query → batch load all chat_message embeddings for this instance_id → cosine search → return top_k
- Same VectorStore pattern (in-memory cache + cosine) can be reused

**Advantage:** No new table needed. Existing infrastructure handles model-awareness, batch ops, and the bridge pattern.

**Consideration:** `embeddings_store` uses `entity_id` as a string key with unique constraint on (entity_type, entity_id, model). For chat messages, we'd have many entries per instance — need to ensure batch loading by prefix (`entity_id LIKE '{instance_id}_%'`) is performant. May need an additional index on entity_id prefix or a separate table if volume is high.

---

## 10. Implementation Details & Principles

### 10.1 Migration — Existing EverMemOS Data

Current episodes have `group_id = narrative_id`. After decoupling, new writes use `group_id = agent_id`. 

**Problem:** Old episodes won't be found by new search (group_id mismatch). An agent could have hundreds of episodes under dozens of narrative_ids.

**Options:**
- **(a) Let old data age out** — new episodes build up under agent_id. Old episodes become invisible. Simplest but loses history.
- **(b) Backfill via EverMemOS API** — `DELETE` old episodes + re-write with new group_id. Requires iterating all narrative_ids per agent, loading episodes, re-posting. Possible but heavy.
- **(c) Direct MongoDB update** — update `group_id` field in MongoDB collections (episodic_memories, memcells, etc.) from narrative_id to agent_id. Fast batch operation but bypasses EverMemOS's indexing (ES + Milvus would be stale).
- **(d) Dual search transition** — for a period, search both `group_id=agent_id` AND `group_id` in `agent_narrative_ids`. Merge results. Remove after backfill.
- **(e) Accept different groups coexist** — old data under narrative_ids, new data under agent_id. EverMemOS search returns both if we search by `user_id` without `group_id` filter. But this re-introduces the cross-agent pollution problem.

**Decision:** Provide a **per-agent migration function** that:
1. Takes an `agent_id` as input
2. Looks up all `narrative_ids` belonging to that agent (from MySQL narratives table)
3. For each narrative_id, finds all episodes in EverMemOS with `group_id = narrative_id`
4. Re-writes them with `group_id = agent_id`
5. Shows a progress bar (e.g., `Migrating agent_xxx: 45/120 episodes [37%]`)

This allows targeted migration for important agents while leaving unimportant ones to build up new episodes naturally. Not all agents need migration — new agents or low-activity agents can start fresh.

```python
async def migrate_agent_episodes(agent_id: str) -> int:
    """Migrate an agent's EverMemOS episodes from group_id=narrative_id to group_id=agent_id.
    Returns number of episodes migrated."""
    # 1. Get all narrative_ids for this agent
    # 2. For each narrative_id: search EverMemOS, re-write with new group_id
    # 3. Progress bar per narrative
```

### 10.2 Logging Principles

For the new process, create distinct log messages so we can trace the decoupled flow:

```
[NarrativeSelect] — narrative selection logs (continuity, VectorStore, LLM judge)
[EverMemOS-Search] — episode search logs (query, top_k, results count, latency)
[EverMemOS-Write]  — episode write logs (event_id, group_id=agent_id, success/fail)
[ContextAssembly]  — context building logs (each section size, total size, dedup count)
[ChatHistory-A]    — recent messages loaded (count, narrative_id)
[ChatHistory-B]    — embedding retrieval (query, instance_id, top_k, results)
[ShortTermMemory]  — short-term messages loaded (count, source instances)
[Dedup]            — dedup results (removed count, from which source)
```

Each log should include timing (`{elapsed:.1f}s`) for performance tracking.

### 10.3 Part B — Chat Message Embedding Retrieval

**Approach: Follow the existing Event embedding pattern.**

The `events` table already has `event_embedding` (List[float]) and `embedding_text` (str) columns, generated in `EventProcessor._generate_embedding()`. This is the exact pattern we need for chat messages.

**Current event embedding flow:**
```
Step 4.3: update_event_in_db(generate_embedding=True)
  → EventProcessor.update_event()
    → _generate_embedding(input_content, final_output)
      → combines user input + agent output, truncates to 500 chars
      → calls get_embedding(text) → 1536-dim vector
      → stores in events.event_embedding + events.embedding_text
```

**For chat messages, we can either:**

**(a) Reuse event embeddings directly** — each event already has an embedding of (user_input + agent_output). Since there's a 1:1 mapping between events and chat message pairs (ChatModule saves one pair per event in `hook_after_event_execution`), we can use `event_embedding` for Part B retrieval. This requires NO new embedding generation or storage.

Flow:
```
Part B retrieval:
  1. Get current narrative's event_ids from narrative.event_ids
  2. Load event embeddings from events table (batch query)
  3. Cosine similarity against query_embedding
  4. Return top_k events → map back to their message content
```

**Advantage:** Zero new infrastructure. Event embeddings already exist and are generated every turn.

**Disadvantage:** Event embeddings are based on (input[:250] + output[:250]) — may not perfectly represent the full conversation content. Also requires loading from events table, not ChatModule memory directly.

**(b) Use `embeddings_store` with entity_type="chat_message"** — as described in Section 9. New embedding per message pair.

**Decision: Create a new messages table** — not reuse event table. Each row stores a message pair in the format used for context building (e.g., `"User: ...\nAssistant: ..."`), plus an embedding vector. This keeps the message representation consistent with how it appears in the prompt, and separates message storage from the event table's audit-trail purpose.

New table (design TBD in implementation):
```sql
chat_message_embeddings:
  id              BIGINT AUTO_INCREMENT
  instance_id     VARCHAR(128) NOT NULL   -- ChatModule instance (= user_id + narrative_id)
  message_index   INT NOT NULL            -- position in messages array
  content         TEXT NOT NULL            -- "User: ...\nAssistant: ..." formatted for context
  embedding       JSON                    -- 1536-dim vector
  source_text     VARCHAR(512)            -- text used for embedding (may be truncated)
  created_at      DATETIME(6)
  INDEX idx_instance (instance_id)
```

Write: in ChatModule `hook_after_event_execution`, after saving the message pair, also embed and store.
Read: embed query → load all embeddings for instance_id → cosine search → return top_k.

### 10.4 EverMemOS Disabled Behavior

When `EVERMEMOS_ENABLED = False`:
- Relevant Memory section = empty (no episodes)
- Narrative selection = VectorStore only (already works)
- Everything else unchanged (ChatModule DB for conversation history, short-term memory)
- System fully functional, just without semantic episode enrichment

No special fallback code needed — the parallel task for EverMemOS simply returns an empty list.

### 10.5 What NOT to change in this decoupling

- **Narrative model** — no field changes needed
- **ChatModule write path** — still saves messages the same way
- **Session management** — unchanged
- **Module instructions** — unchanged
- **Continuity detection** — unchanged (already doesn't use EverMemOS)
- **VectorStore** — unchanged (still used for narrative selection)
- **Step 4 persistence** — unchanged (event creation, narrative update)
- **Step 5 hooks** — MemoryModule still writes to EverMemOS, just with different group_id

---

## Resolved Questions

- [x] **`_aggregate_by_narrative`?** — Remove from EverMemOS client. If needed in future for ChatModule histories from multiple narratives (e.g., multiple main narratives), create a new utility in context assembly. Not needed now since we only have one main narrative.
- [x] **Migration?** — Per-agent migration function with progress bar. Targeted migration for important agents, others build up fresh. See Section 10.1.
- [x] **Relevant Memory: summaries or full text?** — Use **both** full episode summary and episode content. No budget limit for now. Upper limit only if total exceeds a hard cap.
- [x] **EverMemOS disabled?** — Relevant Memory section = empty. Everything else works. No special fallback code needed.
- [x] **Part B storage?** — New dedicated `chat_message_embeddings` table. Not `embeddings_store` (wrong granularity) or events table (different purpose). See Section 10.3.

## Context Length Principle (for now)

All message sources use **full content** with no truncation. Only apply upper limits when needed:
- Short-term memory: full messages, cut at 15 messages OR 40000 chars (whichever first)
- Relevant Memory (EverMemOS): full episodes, cut at top_k=20 episodes
- Conversation History Part A: full messages, cut at 30 messages
- Conversation History Part B: full messages, cut at top_k results
- Narrative metadata: full summary (compaction is separate concern, not truncation at assembly)
- Module instructions: full instructions per module

Budget allocation across sections is a **follow-up task** after decoupling is complete.
