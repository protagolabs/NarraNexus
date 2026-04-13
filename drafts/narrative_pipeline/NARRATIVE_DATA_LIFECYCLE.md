# Narrative Data Lifecycle — Where Data Lives and How It Flows

> Last updated: 2026-03-31
> Purpose: Trace what happens to data at each stage of a conversation turn

---

## The 5 Data Stores

| Store | Location | Keyed by | What it holds | Written when | Read when |
|-------|----------|----------|---------------|-------------|-----------|
| **Narrative** | MySQL `narratives` table | `narrative_id` | Routing metadata: name, description, current_summary, topic_keywords, routing_embedding, actors, event_ids | Updated after every turn (Step 4.4) | Narrative selection (Step 1), context building |
| **ChatModule Memory** | MySQL `instance_json_format_memory_chat` | `instance_id` (= chat instance) | Raw message pairs: `[{role: "user", content: "..."}, {role: "assistant", content: "..."}]` | After every turn (Step 5 hook) | Long-term memory loading (continuity path fallback) |
| **EverMemOS** | MongoDB + Elasticsearch + Milvus (external) | `group_id` (= narrative_id) + `user_id` | Episodes: semantically processed conversation summaries + embeddings | After every turn (Step 5 MemoryModule hook) | Narrative retrieval (non-continuous path), long-term memory (non-continuous path) |
| **Session** | JSON file `sessions/{agent_id}_{user_id}.json` | `(agent_id, user_id)` | Current state: `current_narrative_id`, `last_query`, `last_response`, `last_query_time` | After narrative selection (Step 1) | Continuity detection, session reuse |
| **VectorStore** | In-memory dict (loaded from MySQL) | `narrative_id` | Cached `routing_embedding` vectors for cosine search | When narrative embedding updates (every 5 events) | Native vector search (continuity auxiliary, fallback path) |

---

## One Conversation Turn — Complete Data Trace

### User sends: "check the budget for Project X"

---

### Phase 1: ROUTING — "Which narrative does this belong to?"

```
User message arrives via WebSocket
    │
    ▼
[Session Load] Read sessions/agent_abc_user_hong.json
    → Found: current_narrative_id = "nar_123", last_query = "show me the timeline"
    → last_query_time = 3 minutes ago (< 600s timeout, session valid)
    │
    ▼
[Continuity Detection] LLM call (gpt-4o-mini)
    Reads from:
      Session     → last_query, last_response
      Narrative   → name, description, current_summary, topic_keywords (loaded from MySQL by nar_123)
    │
    ├── Continuous (same topic) ──────────────────────────────────────┐
    │   Main narrative = nar_123 (from session)                      │
    │   Auxiliary search via VectorStore (in-memory cosine)           │
    │   EverMemOS NOT called (no query_text passed)                  │
    │   evermemos_memories = {} (empty)                               │
    │                                                                 │
    └── NOT continuous (topic switch) ───────────────────────────────┐│
        Full retrieval pipeline:                                     ││
        EverMemOS search (query_text + user_id)                      ││
          → Returns episodes grouped by narrative_id                 ││
          → Scores, summaries, contents per narrative                ││
          → Client-side filter by agent_narrative_ids                ││
        OR VectorStore fallback (if EverMemOS empty/disabled)        ││
        evermemos_memories = {nar_id: {episode_contents, ...}}       ││
                                                                     ││
        Score check → LLM judgment (if < 0.70)                      ││
          Reads from:                                                ││
            Narrative (MySQL) → name, summary (truncated)            ││
            EverMemOS         → episode_summaries (as matched_content)│
            Default narratives (MySQL) → name, description, examples ││
                                                                     ▼▼
                                                        Selected: narrative_list (1 main + 0-2 auxiliary)
```

**Data read during routing:**

| Data | Source store | Continuity path | Non-continuous path |
|------|-------------|-----------------|---------------------|
| Session state | Session file | Yes | Yes |
| Narrative metadata | MySQL narratives | Yes (for continuity LLM) | Yes (for LLM judge) |
| routing_embedding | VectorStore (memory) | Yes (auxiliary search) | Only if EverMemOS fallback |
| Episode scores | EverMemOS | **No** | Yes |
| Episode summaries | EverMemOS | **No** | Yes (in LLM judge as matched_content) |

---

### Phase 2: MEMORY LOADING — "What does the agent need to remember?"

```
Narrative selected (e.g., nar_123)
    │
    ▼
[ChatModule.hook_data_gathering()] Loads two types of memory:

    ┌─── Long-term memory (current narrative's history) ───┐
    │                                                       │
    │  IF evermemos_memories has data for nar_123:          │
    │    → Load episode_contents (up to 30)                 │
    │    → Injected as role="context" messages               │
    │    → Source: EverMemOS episodes                        │
    │                                                       │
    │  ELSE (fallback — continuity path always hits this):  │
    │    → Load from instance_json_format_memory_chat        │
    │    → Raw message pairs, last 40 messages               │
    │    → Source: ChatModule DB                             │
    └───────────────────────────────────────────────────────┘

    ┌─── Short-term memory (other narratives' recent msgs) ─┐
    │                                                        │
    │  Load ChatModule instances for OTHER narratives         │
    │  (same agent_id + user_id, excluding current)           │
    │  Take 15 most recent messages across all                │
    │  Source: ChatModule DB (always)                          │
    │  No relevance filtering — purely recency-based          │
    └────────────────────────────────────────────────────────┘
    │
    ▼
Both merged into ctx_data.chat_history
```

**What the agent actually sees in its prompt:**

```
System Prompt:
  [Part 1] Narrative info (name, description, summary, actors)  ← from MySQL narrative
  [Part 2] Event History (DISABLED)
  [Part 3] Auxiliary narrative summaries + episode summaries     ← from MySQL + evermemos_memories
  [Part 4] Module instructions (Chat, BasicInfo, Awareness, Social, Job, Matrix, Skill, RAG)
  [Part 5] Bootstrap injection (first 3 turns only)
  [Appended] Short-term memory (15 recent msgs from OTHER narratives) ← from ChatModule DB

Conversation messages:
  Long-term memory: either EverMemOS episodes OR ChatModule DB messages ← depends on path
  Current user message: "check the budget for Project X"
```

---

### Phase 3: AGENT EXECUTION — "Agent thinks and responds"

```
Claude CLI agent loop runs
  → Reads system prompt + conversation messages
  → Calls MCP tools (send_message_to_user_directly, job_create, matrix_send, etc.)
  → Produces: final_output, tool calls, thinking trace
    │
    ▼
No data store writes happen during execution
(All writes happen in Phase 4 and 5)
```

---

### Phase 4: PERSISTENCE — "Save execution results"

```
Step 4 (synchronous, blocks WebSocket)
    │
    ├── 4.1 Record Trajectory → trajectory file (execution trace)
    ├── 4.2 Update Markdown Stats → markdown file
    ├── 4.3 Update Event in DB → MySQL events table
    │       → Generates event_embedding (input + output, 500 chars)
    │       → Writes embedding to DB (but NOT synced to in-memory event object)
    │       → Syncs final_output to ctx.event (for hooks to read)
    ├── 4.4 Update Narratives → MySQL narratives table
    │       For MAIN narrative:
    │         → Append event_id to event_ids
    │         → Append to dynamic_summary (final_output[:200] placeholder)
    │         → Trigger async LLM update (every 1 event):
    │             → Updates: name, current_summary, topic_keywords
    │             → Input: current narrative info + last 5 dynamic_summary entries + latest event
    │             → Does NOT update: description (frozen forever), actors (preserved from DB)
    │         → Trigger async embedding update (every 5 events):
    │             → Regenerates topic_hint = name + summary (truncated to 800 chars)
    │             → Embeds topic_hint → updates routing_embedding
    │             → Updates VectorStore in-memory cache
    │       For AUXILIARY narratives:
    │         → Only append event_id + placeholder dynamic_summary
    │         → No LLM update, no embedding update
    │       For DEFAULT narratives:
    │         → Only append event_id
    ├── 4.5 Update Session → MySQL + JSON file
    │       → session.last_response = final_output
    └── 4.6 Record LLM Cost → MySQL cost_records table
```

**Data written during persistence:**

| Data | Target store | What's written |
|------|-------------|----------------|
| Event record | MySQL events | event_id, final_output, event_log, event_embedding |
| Narrative metadata | MySQL narratives | event_ids, dynamic_summary, (async: name, summary, keywords, embedding) |
| Session | JSON file | last_response |
| Cost | MySQL cost_records | tokens, cost_usd |
| VectorStore cache | In-memory | routing_embedding (async, every 5 events) |

---

### Phase 5: POST-PROCESSING — "Background hooks update memory stores"

```
Step 5 + 6 (background, non-blocking — user already got response)
    │
    ▼
All module hooks run in parallel:

    ┌─── ChatModule hook ──────────────────────────────────┐
    │ Reads: params.input_content, assistant response       │
    │ Writes to: instance_json_format_memory_chat (MySQL)   │
    │   → Appends {role: "user", content: "..."} +          │
    │     {role: "assistant", content: "..."}                │
    │   → This is the DB that continuity-path reads from    │
    └───────────────────────────────────────────────────────┘

    ┌─── MemoryModule hook ────────────────────────────────┐
    │ Reads: event object, narrative object                  │
    │ Writes to: EverMemOS (external HTTP POST)              │
    │   1. POST /conversation-meta (once per narrative)      │
    │      → group_id = narrative_id                         │
    │      → user_details, tags: ["narrative", agent_id]     │
    │   2. POST /memories (2 messages: user + agent)         │
    │      → sender = user_id, group_id = narrative_id       │
    │      → EverMemOS will later process into episodes      │
    │                                                        │
    │ Note: EverMemOS processes messages asynchronously:     │
    │   raw messages → MemCell boundary detection            │
    │   → episode extraction (summary + narrative)           │
    │   → vector indexing (Milvus + Elasticsearch)           │
    │   → available for search on next query                 │
    └────────────────────────────────────────────────────────┘

    ┌─── SocialNetworkModule hook ─────────────────────────┐
    │ LLM calls to extract entities from conversation       │
    │ Writes to: MySQL (social network entities)            │
    └───────────────────────────────────────────────────────┘

    ┌─── JobModule hook ───────────────────────────────────┐
    │ LLM evaluates if ongoing jobs met end conditions      │
    │ Writes to: MySQL (job status updates)                 │
    └───────────────────────────────────────────────────────┘

    ┌─── MatrixModule hook ────────────────────────────────┐
    │ Marks rooms as read (Matrix API call)                 │
    └───────────────────────────────────────────────────────┘
```

**Data written during post-processing:**

| Data | Target store | What's written |
|------|-------------|----------------|
| Chat message pairs | ChatModule DB (MySQL) | user input + assistant response |
| Raw messages | EverMemOS (HTTP) | 2 messages → will become episodes later |
| Entity info | MySQL social entities | Names, roles, relationships |
| Job status | MySQL jobs | Completion evaluation |

---

### Phase 6: SESSION UPDATE — "Remember where we left off"

```
Session already updated in Step 4.5 with last_response.
Session was updated in Step 1 with:
  → current_narrative_id = selected narrative
  → last_query = user input
  → last_query_time = now
  → query_count += 1

Ready for next turn.
```

---

## The Same Information in Multiple Places

After one turn, the conversation content exists in **three** places:

| Store | Format | When available for retrieval |
|-------|--------|-----------------------------|
| **ChatModule DB** | Raw: `{role: "user", content: "check the budget"}` + `{role: "assistant", content: "Budget is $50k"}` | Immediately (written in Step 5 hook) |
| **EverMemOS** | Processed: episode summary like "User inquired about Project X budget. Agent confirmed $50k allocation." | After EverMemOS background processing (seconds to minutes) |
| **Event record** | Structured: event_log (tool calls, thinking), final_output, event_embedding | Immediately (written in Step 4.3), but currently not used in prompt (Event History disabled) |

**ChatModule DB** is the source of truth for raw conversation. **EverMemOS** is the semantic layer on top. **Event record** is the audit trail. They're written independently and never cross-reference each other.

---

## When Each Store Is Read (by path)

| Scenario | Session | Narrative (MySQL) | VectorStore | EverMemOS | ChatModule DB |
|----------|---------|-------------------|-------------|-----------|---------------|
| **Continuity path** (same topic) | current_narrative_id, last_query/response | name, summary, keywords (for continuity LLM) | routing_embedding (auxiliary search) | **Not used** | Long-term memory (last 40 msgs) + Short-term (15 from others) |
| **Non-continuous path** (topic switch) | last_query_time (for timeout check) | name, summary (for LLM judge, truncated) | Only if EverMemOS fallback | Episodes (for scoring + LLM judge + long-term memory) | Short-term memory (15 from others) |
| **New session** (first msg or timeout) | Creates new | name, summary (for LLM judge) | Only if EverMemOS fallback | Episodes (same as non-continuous) | Short-term memory (15 from others) |
| **Job trigger** | Not used | Forced narrative loaded directly | Not used | Not used | Depends on evermemos_memories (usually empty for jobs) |

---

## Key Inconsistencies in the Data Flow

1. **Continuity path skips EverMemOS entirely** — the most common path (same topic, next turn) uses only ChatModule DB for long-term memory (last 40 raw messages). EverMemOS's richer semantic episodes are only used on topic switches.

2. **Same content written twice** — every turn writes to both ChatModule DB (raw messages) and EverMemOS (raw messages → episodes). They can overlap in short-term memory vs long-term memory, with no dedup.

3. **Narrative metadata updated async but read sync** — LLM updates name/summary every turn (async background), but the next turn's continuity detector reads the narrative synchronously. If async hasn't finished, continuity reads stale metadata.

4. **Event embedding generated but orphaned** — Step 4.3 creates event_embedding in DB, but it's not synced to the in-memory event object. The background hooks (Step 5) can't access it. It's only used in `_enhance_with_events()` which loads it separately from DB on the next retrieval.

5. **Routing embedding vs EverMemOS scores** — two separate ranking systems that don't share information. VectorStore uses routing_embedding (text snapshot every 5 events). EverMemOS uses RRF on episodes. They're used in different paths and never blended (until our centroid addition).

6. **Truncation appears at random points** — some places have limits (summary 300 chars for LLM judge, topic_hint 200/800 chars for embedding, episode summaries 500 chars), others have none (continuity detector sees full summary, full awareness). No consistent policy.
