# Narrative Retrieval Pipeline — Full Process Review

> Last updated: 2026-03-24

## Overview

When a user sends a message, the system must decide **which Narrative** (conversation context) to use. This pipeline handles retrieval, scoring, LLM judgment, and context injection.

```
User Message
    │
    ▼
[1] Session Check ─── (agent_id, user_id) → active session?
    │
    ▼
[2] Continuity Detection ─── LLM: does this query belong to current narrative?
    │
    ├─ YES → load current narrative + auxiliary Top-K
    │
    ▼─ NO
[3] Retrieval ─── EverMemOS or vector search → candidate narratives + scores
    │
    ▼
[4] Judgment ─── high score? direct match. Low score? LLM picks or creates new
    │
    ▼
[5] Post-Selection ─── ensure ChatModule instances, update session, build prompt
    │
    ▼
[6] Write-Back ─── (after agent responds) MemoryModule writes event to EverMemOS
```

---

## Step 1: Session Check

**File:** `narrative/session_service.py`
**Method:** `get_or_create_session(user_id, agent_id)`

| Aspect | Detail |
|--------|--------|
| **Scope** | One session per `(user_id, agent_id)` pair |
| **Storage** | In-memory cache + JSON file: `{session_dir}/{agent_id}_{user_id}.json` |
| **Timeout** | 600 seconds (configurable in `narrative/config.py`) |
| **Key fields** | `current_narrative_id`, `last_query`, `last_response`, `last_query_embedding`, `query_count` |

**Logic:**
1. Check in-memory cache by `(user_id, agent_id)`
2. If miss, load from JSON file
3. If `elapsed > 600s`, delete old session, create new
4. If no session found, create new (no `current_narrative_id`)

**When session exists with `current_narrative_id`** → goes to Continuity Detection.
**When session is new** → skips continuity, goes straight to Retrieval.

---

## Step 2: Continuity Detection

**File:** `narrative/_narrative_impl/continuity.py`
**Method:** `detect(current_query, session, current_narrative, awareness)`
**LLM model:** `gpt-4o-mini` (configurable: `CONTINUITY_LLM_MODEL`)

**Skipped when:** No previous query in session (new session).

### What the LLM receives:

```
Current Narrative Information:
- Name: {narrative.name}
- Description: {narrative.description}
- Current Summary: {narrative.current_summary}
- Topic Keywords: {narrative.keywords}

Agent Awareness:
{awareness text if provided}

Previous conversation turn:
User asked: {session.last_query}
Agent responded: {session.last_response}
[Time elapsed: X minutes]

New query from user:
{current_query}

Question: Does this new query belong to the current Narrative?
```

### What the LLM returns:

```json
{
  "is_continuous": true/false,
  "confidence": 0.0-1.0,
  "reason": "explanation"
}
```

**Special handling:** Matrix messages have channel tags stripped before LLM sees them — only core content is used for continuity judgment.

### Input limits:
- **No truncation applied** on any field (query, response, narrative summary, awareness).
- All passed at full length to LLM. In practice this is fine since most responses aren't huge.
- If narrative summaries grow too long, the fix should be compacting the summary itself (in the narrative update step), not truncating here.

### Design notes:
- Only the **last turn** (previous query + response) is included, not all consecutive turns in the same narrative. Last turn is sufficient signal for topic continuity.
- **Awareness text** is included but has low value for continuity judgment (agent personality rarely affects whether a query belongs to the same topic). **Consider removing** to save tokens — noted for future optimization.
- Continuity runs on **every turn** regardless of time gap. Time-based skipping was considered but rejected: complex tasks (multi-tool, agent-to-agent) can take minutes between messages while still being active, and topic switches can happen mid-active-conversation.

### Outcome:

| Result | Next step |
|--------|-----------|
| `is_continuous = true` | Load current narrative + retrieve auxiliary Top-K (vector search only, no LLM) |
| `is_continuous = false` | Full retrieval pipeline |

---

## Step 3: Retrieval

**File:** `narrative/_narrative_impl/retrieval.py`
**Method:** `retrieve_top_k(query, user_id, agent_id, top_k)`

### Sub-steps:

### 3.1 Ensure Default Narratives
- Creates 8 default narratives per `(agent_id, user_id)` if not exist
- Types: greetings, casual chat, jokes, help, configuration, etc.
- File: `_narrative_impl/default_narratives.py`

### 3.2 Query Participant Narratives
- Finds narratives where `user_id` is a PARTICIPANT (not creator)
- Use case: sales — customer accesses a shared narrative created by the agent's owner
- These won't appear in vector search (different creator), so they're fetched explicitly

### 3.3 Generate Query Embedding
- Model: `text-embedding-3-small` (OpenAI)
- Used for both EverMemOS and native vector search

### 3.4 Search (EverMemOS or Vector Store)

**Path A: EverMemOS (if enabled)**

```
GET /api/v1/memories/search
  ?query={text}
  &top_k={top_k * 3}          ← fetch 3x, reduce after aggregation
  &memory_types=episodic_memory
  &retrieve_method=rrf         ← Reciprocal Rank Fusion: BM25 + Vector + fusion
  &user_id={user_id}           ← server-side user isolation
```

**Response structure:**
```json
{
  "result": {
    "memories": [{"group_id": [episode1, episode2, ...]}, ...],
    "scores": [{"group_id": [score1, score2, ...]}, ...],
    "pending_messages": [{"group_id": "...", ...}, ...]
  }
}
```

**Post-processing (`_aggregate_by_narrative`):**
1. Filter episodic_memory by `agent_narrative_ids` (from local DB)
2. Filter pending_messages by `agent_narrative_ids`
3. Take max score per `narrative_id` (group_id)
4. Normalize RRF scores: `score × 10.0`, capped at `0.95`
5. Extract `episode_summaries` (max 5 per narrative) — used in LLM judgment
6. Extract `episode_contents` (max 30 per narrative) — used for short-term memory dedup

**Score mapping (RRF raw → normalized):**
```
0.10+ → 0.95 (capped)     ultra-high match
0.07  → 0.70              high confidence (above threshold)
0.05  → 0.50              medium (goes to LLM judgment)
0.016 → 0.16              low
```

**Path B: Native Vector Store (fallback)**

```python
VectorStore.search(
    query_embedding=embedding,
    filters={"user_id": user_id, "agent_id": agent_id},
    top_k=top_k,
    min_score=0.0
)
```
- In-memory vector store loaded from DB
- Cosine similarity
- Filters applied via metadata match

### 3.5 Merge Participant Narratives
- Calculate cosine similarity for participant narratives against query embedding
- Insert into candidate list, re-sort by score

### 3.6 Enhance with Recent Events (vector search only)
- For each candidate narrative, load last 5 events
- Compute average embedding of event texts
- Blend: `final_score = topic_score × 0.8 + events_score × 0.2`
- Skipped when using EverMemOS (episodes already capture this)

---

## Step 4: Judgment

**File:** `narrative/_narrative_impl/_retrieval_llm.py`

### Tier 1: High Confidence (no LLM)
- If `best_score >= 0.70` AND no participant narratives
- Return `search_results[:top_k]` directly (up to 3 narratives: 1 main + 2 auxiliary)
- Method: `"high_confidence"`
- No LLM call — saves ~500ms-1s

### Tier 2: LLM Judgment
- If score < 0.70 OR has participant narratives (forced even if score is high)
- Method: `"llm_confirmed"`
- Model: `gpt-4o-mini` (configurable: `NARRATIVE_JUDGE_LLM_MODEL`)
- Receives at most 3 search candidates (`search_results[:3]`)

### What the LLM receives (with exact character limits):

```
## Participant-Associated Topics (user is a PARTICIPANT):

[Participant-0] {topic_hint[:50]}                          ← ONLY topic_hint, 50 char limit
Description: {topic_hint[:100]}                            ← same topic_hint, 100 char limit
                                                           ← NO narrative_info.name
                                                           ← NO narrative_info.current_summary
                                                           ← NO narrative_info.description

## Default Topic Types:

[Default-0] {narrative_info.name}                          ← full name, no limit
Description: {narrative_info.description}                  ← full description, no limit
Examples: Hello, Good morning, Thanks                      ← up to 3 examples from config

## Existing Topics:

[Topic-0] {narrative_info.name or topic_hint[:50]}         ← name preferred, fallback 50 chars
Description: {narrative_info.current_summary[:300]}        ← ⚠️ TRUNCATED to 300 chars
            {or topic_hint[:100] as fallback}              ← fallback 100 chars
Similarity score: 0.65
Matched content:                                           ← episode_summaries joined by \n
{episode_summaries, max 500 chars total}                   ← ⚠️ TRUNCATED to 500 chars

## User's New Query:
{query}                                                    ← full query, no limit
```

### Character limit summary:

| Candidate type | name source | name limit | description source | desc limit | extra |
|----------------|-------------|------------|-------------------|------------|-------|
| **Participant** | `topic_hint` | **50 chars** | `topic_hint` | **100 chars** | none |
| **Default** | `narrative_info.name` | no limit | `narrative_info.description` | no limit | 3 examples |
| **Search** | `narrative_info.name` or `topic_hint` | 50 (fallback) | `narrative_info.current_summary` | **300 chars** | episode summaries **500 chars** |

### Information loss at each truncation point:

- **Participant 50/100 chars**: `narrative_info` (name, description, current_summary) is **completely ignored**. Only `topic_hint` is used. If the narrative has a rich summary built over many turns, the LLM sees none of it.
- **Search description 300 chars**: `current_summary` grows unbounded over conversation turns (no compaction). A narrative with 20+ turns could have a 3000+ char summary — the LLM sees only the first 10%. It may miss key context about what the narrative covers.
- **Search matched_content 500 chars**: If a narrative has 5 episode summaries averaging 200 chars each (1000 total), half gets cut.

### What the LLM returns:

```json
{
  "reason": "explanation",
  "matched_category": "default" | "search" | "participant" | "none",
  "matched_index": 0
}
```

### Decision and what gets returned:

| `matched_category` | Narratives returned | Auxiliary? |
|---------------------|---------------------|------------|
| `"participant"` + valid index | 1 matched participant narrative | **No auxiliary** |
| `"default"` + valid index | 1 matched default narrative | **No auxiliary** |
| `"search"` + valid index | matched + remaining top-K (up to 3) | **Yes, up to 2 auxiliary** |
| `"none"` | 1 newly created narrative | **No auxiliary** |

**Note:** Only "search" matches get auxiliary narratives. Default and participant matches return a single narrative with no cross-topic context.

---

## Step 5: Post-Selection

### 5a. Ensure ChatModule Instances
- For each selected narrative, ensure a `ChatModule` instance exists for `(user_id, narrative_id)`
- Different users in the same narrative get independent chat histories
- File: `step_1_select_narrative.py`, `_ensure_user_chat_instance()`

### 5b. Update Session
```python
session.last_query = input_content
session.last_query_embedding = query_embedding
session.current_narrative_id = narratives[0].id
session.query_count += 1
session.last_query_time = now
```

### 5c. Build Narrative Prompt
- File: `_narrative_impl/prompt_builder.py`
- Generates system prompt section with:
  - Narrative ID, type, name, description, summary
  - Actor list (USER, AGENT, PARTICIPANT with descriptions)
  - Created/updated timestamps

---

## Step 6: Write-Back (Post-Execution, Background)

**File:** `module/memory_module/memory_module.py` → `utils/evermemos/client.py`
**When:** Step 5 hooks (runs in background after WebSocket closes)

### 6a. Ensure Conversation Meta (once per narrative)
```
POST /api/v1/memories/conversation-meta
{
  "group_id": narrative_id,
  "name": narrative_name,
  "description": narrative_description,
  "user_details": {user_id: {full_name, role: "user"}},
  "tags": ["narrative", agent_id]
}
```

### 6b. Write Messages (2 per event: user + agent)
```
POST /api/v1/memories
{
  "message_id": "{event_id}_user",
  "sender": user_id,
  "sender_name": user_id,
  "role": "user",
  "content": input_content,
  "group_id": narrative_id,
  "scene": "assistant"
}
```
```
POST /api/v1/memories
{
  "message_id": "{event_id}_agent",
  "sender": user_id,           ← NOTE: still user_id, not agent_id
  "sender_name": agent_id,     ← agent identity only here
  "role": "assistant",
  "content": final_output,
  "group_id": narrative_id,
  "scene": "assistant"
}
```

---

## Data Scoping Analysis

### Where agent_id / user_id isolation happens:

| Operation | user_id scoping | agent_id scoping | Method |
|-----------|-----------------|-------------------|--------|
| Session lookup | `(user_id, agent_id)` key | `(user_id, agent_id)` key | File path |
| Vector store search | `filters.user_id` | `filters.agent_id` | Metadata match |
| EverMemOS search | `?user_id=` query param (server-side) | `agent_narrative_ids` post-filter (client-side) | Two-layer |
| EverMemOS write | `sender = user_id` | `tags: ["narrative", agent_id]` on meta only | Indirect |
| Default narratives | Per `(agent_id, user_id)` | Per `(agent_id, user_id)` | DB count check |
| Participant narratives | `user_id` as participant | `agent_id` filter | DB query |

### Known Issues:

#### Issue 1: EverMemOS agent isolation is indirect
- **Write:** Individual messages have `sender = user_id` but no `agent_id` field. Agent identity is only in conversation-meta `tags` and `sender_name`.
- **Read:** Agent isolation relies on `agent_narrative_ids` from local DB — if the local DB is inconsistent, cross-agent episodes could leak into results before the post-filter.
- **Risk:** Low in practice (narrative_ids are unique), but not a clean separation.

#### Issue 2: Episode summaries used only for selection, not for prompt
- EverMemOS returns rich `episode_summaries` per candidate narrative
- These are passed to the LLM judge as "Matched content" for scoring
- After a narrative is selected, the episodes are **discarded**
- The actual prompt context comes from:
  - Narrative metadata (name, description, summary)
  - ChatModule chat history (stored in DB, not EverMemOS)
  - Short-term memory (recent messages from **other** narratives)
- EverMemOS episodes don't directly enrich the agent's context — they're a retrieval signal only

#### Issue 3: Short-term memory scope
- Loads recent messages from **other narratives** for same `(agent_id, user_id)`
- Intentional cross-narrative context (helps with "like I just said..." references)
- Now uses 40k char budget (~10k tokens) with no per-message truncation
- Risk: if agent has many active narratives with long messages, this section can be large

---

## LLM Calls Summary

| Step | Model | Purpose | Input size | When called |
|------|-------|---------|------------|-------------|
| Continuity Detection | gpt-4o-mini | "Does query belong to current narrative?" | ~500-2000 chars | Every turn (if session has history) |
| Narrative Judgment | gpt-4o-mini | "Which candidate narrative matches?" | ~1000-5000 chars (candidates + episodes) | When score < 0.70 or has participants |
| Narrative Update (Step 4.4) | gpt-4o-mini | Update narrative summary after event | ~500-2000 chars | Every turn (main narrative only) |

---

## Configuration Reference

**File:** `narrative/config.py`

| Parameter | Value | Purpose |
|-----------|-------|---------|
| `SESSION_TIMEOUT` | 600s | Session expiry |
| `NARRATIVE_MATCH_HIGH_THRESHOLD` | 0.70 | Score above → skip LLM judgment |
| `NARRATIVE_MATCH_USE_LLM` | True | Enable LLM judgment |
| `NARRATIVE_SEARCH_TOP_K` | 3 | Candidates to retrieve |
| `MAX_NARRATIVES_IN_CONTEXT` | 3 | Max narratives returned (1 main + 2 aux) |
| `VECTOR_SEARCH_MIN_SCORE` | 0.0 | Minimum similarity |
| `MATCH_RECENT_EVENTS_COUNT` | 5 | Events for score enhancement |
| `CONTINUITY_LLM_MODEL` | "gpt-4o-mini" | Continuity detection model |
| `NARRATIVE_JUDGE_LLM_MODEL` | "gpt-4o-mini" | Narrative matching model |
| `EVERMEMOS_ENABLED` | True | Master switch for EverMemOS |
| `EVERMEMOS_SEARCH_TOP_K` | 10 | EverMemOS result limit |
| `EVERMEMOS_EPISODE_SUMMARIES_PER_NARRATIVE` | 5 | Summaries per candidate |
| `EVERMEMOS_EPISODE_CONTENTS_PER_NARRATIVE` | 30 | Contents for dedup |

---

---

## Narrative Data Structure

```python
Narrative:
  # Identity
  id: str                          # e.g. "nar_91192c023847"
  type: NarrativeType              # CHAT / TASK / OTHER
  agent_id: str                    # which agent owns this narrative

  # Core Content
  narrative_info: NarrativeInfo
    name: str                      # LLM-generated name (e.g. "Rocket Launch Discussion")
    description: str               # LLM-generated description
    current_summary: str           # LLM-generated summary, updated each turn (UNBOUNDED length)
    actors: List[NarrativeActor]   # participants list
      - {id: "user_123", type: USER}         # creator/owner
      - {id: "agent_abc", type: AGENT}       # the AI agent
      - {id: "user_456", type: PARTICIPANT}  # target user (e.g. sales customer)

  # Routing Index (for retrieval)
  routing_embedding: List[float]   # embedding vector for vector search
  topic_hint: str                  # short topic summary
  topic_keywords: List[str]        # extracted keywords

  # Orchestration
  active_instances: List[ModuleInstance]   # currently active module instances
  event_ids: List[str]                    # all events in this narrative (chronological)
  dynamic_summary: List[DynamicSummaryEntry]  # per-event short summaries

  # Metadata
  created_at, updated_at, round_counter
  is_special: str                  # "default" for built-in narratives, "other" for user-created
```

**Key points:**
- Narrative has an `agent_id` — it belongs to one agent
- But there's NO `user_id` field on the Narrative itself. User ownership is tracked via the `actors` list (USER type = creator/owner)
- PARTICIPANT actors can access the narrative but didn't create it (sales scenario)
- `current_summary` grows over time — no compaction mechanism currently

---

## Narrative Update — What Gets Updated Each Turn

**File:** `narrative/_narrative_impl/updater.py`
**Called from:** Step 4.4 in `step_4_persist_results.py`

### Three paths depending on narrative type:

| Narrative type | event_ids | dynamic_summary | LLM update | Embedding update |
|----------------|-----------|-----------------|------------|------------------|
| **Default** (is_special="default") | append only | No | No | No |
| **Auxiliary** (not main) | append | append `final_output[:200]` placeholder | No | No |
| **Main** | append | append placeholder → replaced by LLM | **Yes** (every turn) | Every 5 turns |

### Main Narrative — Phase 1: Immediate (blocking, Step 4.4)
- `event_ids.append(event.id)`
- `events_since_last_embedding_update += 1`
- `dynamic_summary.append(final_output[:200])` — temporary placeholder
- Save to DB

### Main Narrative — Phase 2: Async LLM Update (`asyncio.create_task`)
**Triggered:** Every `NARRATIVE_LLM_UPDATE_INTERVAL` events (currently **= 1**, i.e. every turn)
**Model:** default (gpt-4o-mini via OpenAIAgentsSDK)

**What the LLM receives:**
```
## Current Narrative Information
- Name: {name}                                  ← full, no limit
- Description: {description}                    ← full, no limit (but frozen at creation, never updated)
- Current Summary: {current_summary}            ← FULL, no truncation, unbounded
- Keywords: {keywords joined}

## Recent Conversation History                  ← last 5 dynamic_summary entries (config: NARRATIVE_LLM_UPDATE_EVENTS_COUNT=5)
1. {summary entry 1}
2. {summary entry 2}
...

## Latest Conversation
User Input: {event.env_context.input}           ← full, no limit
Agent Response: {event.final_output[:500]}      ← ⚠️ TRUNCATED to 500 chars
```

**What the LLM generates** (`NarrativeUpdateOutput`):

| Field | Updated in DB? | Limit (instruction) | Limit (code) |
|-------|---------------|---------------------|--------------|
| `name` | Yes | "3-8 words" | none |
| `current_summary` | Yes | "8-12 bullets, structured" | **none — unbounded** |
| `topic_keywords` | Yes | "5-10 items" | none |
| `actors` | **NO — ignored** | generates but code preserves DB version | N/A |
| `dynamic_summary_entry` | Yes (replaces last placeholder) | "one short sentence" | none |

**Fields NOT updated by LLM:**
- `description` — **frozen at creation**, never updated. A narrative that evolved from "quick Python question" to a deep multi-session project keeps its original description.
- `actors` — LLM output ignored to avoid overwriting concurrent PARTICIPANT additions.
- `topic_hint` — only regenerated during embedding update (Phase 3).

### Main Narrative — Phase 3: Embedding Update (conditional, async)
**Triggered:** Every `EMBEDDING_UPDATE_INTERVAL` events (currently **= 5**)

1. Regenerate `topic_hint` = `"{name}: {summary}"` truncated to **200 chars** (`SUMMARY_MAX_LENGTH`)
2. Generate new embedding from `topic_hint`
3. Update `routing_embedding`, `embedding_updated_at`, reset counter
4. Update VectorStore in-memory cache

### Config values:

| Parameter | Value | Purpose |
|-----------|-------|---------|
| `NARRATIVE_LLM_UPDATE_INTERVAL` | 1 | LLM update every N events (1 = every turn) |
| `NARRATIVE_LLM_UPDATE_EVENTS_COUNT` | 5 | Recent dynamic_summary entries in LLM context |
| `EMBEDDING_UPDATE_INTERVAL` | 5 | Embedding update every N events |
| `SUMMARY_MAX_LENGTH` | 200 | `topic_hint` max chars (for embedding) |

### Problems with current update logic:

**Problem U1: `current_summary` grows unbounded**
- LLM instructed "max 8-12 bullets" but nothing enforces it in code
- Over many turns, summary accumulates. No compaction mechanism.
- This inflates: (a) the LLM update context itself (feeds back full summary), (b) the system prompt narrative section, (c) continuity detection input.

**Problem U2: `topic_hint` is only 200 chars**
- Combined `name + summary` truncated to 200 chars for embedding
- A narrative with a rich 2000-char summary is represented by 200 chars in vector search
- Same `topic_hint` is what participant candidates show to the LLM judge (truncated to 50/100 chars on top of this)

**Problem U3: `description` is frozen forever**
- Created once when narrative is first made, never updated by LLM
- If the narrative evolves, description becomes stale/misleading

**Problem U4: Agent response truncated to 500 chars in LLM update context**
- If the agent wrote a 3000-char detailed response, the updater LLM only sees 500 chars
- Summary may miss important details from the response

**Problem U5: Embedding only updates every 5 turns**
- After a topic shift within a narrative, the vector search index is stale for up to 4 turns
- Combined with continuity detection, this is usually fine (continuity handles the immediate turns)
- But if continuity detection fails, stale embedding could route to wrong narrative

**Problem U6: Auxiliary narratives never get LLM updates**
- Only basic event_id + placeholder dynamic_summary
- Name, summary, keywords stay as they were when selected as auxiliary
- Over time, auxiliary narrative metadata drifts from reality

---

## What Goes Into the Final Prompt (from selected narrative)

After narrative selection, the system prompt is built from these parts:

```
System Prompt =
  [Part 1] Main Narrative Info (name, description, summary, actors)
  [Part 2] Event History (DISABLED — currently commented out)
  [Part 3] Auxiliary Narratives (name, summary, event count + EverMemOS episode_summaries as "Related Content")
  [Part 4] Module Instructions (ChatModule, BasicInfo, Awareness, SocialNetwork, Job, Matrix, Skill, RAG)
  [Part 5] Bootstrap Injection (first 3 turns only, creator only)

Then appended:
  [Short-Term Memory] Recent messages from OTHER narratives (cross-narrative context)

Then as conversation messages (not system prompt):
  [Long-Term Memory] Chat history from current narrative's ChatModule instance
  [User Message] Current turn input
```

### What comes from EverMemOS in the prompt:
- **Auxiliary narrative "Related Content"**: `episode_summaries` (max 3 per auxiliary narrative, truncated to 150 chars) — from `_build_auxiliary_narratives_prompt()`
- That's it. EverMemOS episodes are NOT injected into the main narrative prompt or chat history.

### What comes from ChatModule DB:
- **Long-term memory**: Full chat history of the current narrative (from `instance_json_format_memory_chat` table)
- **Short-term memory**: Recent messages from OTHER narratives (max 15 messages, from `_load_short_term_memory()`)

### episode_contents dedup — current status:
- `episode_contents` (max 30 per narrative) are extracted from EverMemOS and passed via `evermemos_memories` cache
- Intended purpose: dedup against short-term memory to avoid showing the same content twice
- **Actual status: NOT IMPLEMENTED.** The short-term memory builder (`_build_short_term_memory_prompt`) does not reference `episode_contents`. The dedup never happens.
- Short-term memory comes from ChatModule DB (other narrative chat histories), not from EverMemOS, so they're different data sources anyway. The overlap would only occur if the same conversation was in both places.

---

## Problems Found & Ideas

### Problem 1 (SERIOUS): No agent_id in EverMemOS search
- Search only sends `user_id` → EverMemOS returns episodes from ALL agents serving this user
- Client-side post-filter by `agent_narrative_ids` catches it, but:
  - RRF scoring happens server-side BEFORE the filter — ranking is polluted by cross-agent episodes
  - A high-scoring episode from another agent could push the real match lower
  - `top_k * 3` is a hack to compensate, but fundamentally insufficient if user has many agents
- **Root cause of Problem 2**
- **Fix**: Either pass agent_id/agent_narrative_ids to EverMemOS API for server-side filtering, or tag episodes with agent_id on write

### Problem 2: `top_k * 3` over-fetching hack
- Compensates for Problem 1 by fetching 3x more candidates
- Wasteful when user has 1 agent, insufficient when user has 10+
- Goes away if Problem 1 is fixed

### Problem 3: Max-score-per-narrative ignores breadth of relevance
- Takes single highest episode score to represent entire narrative
- One lucky keyword match can inflate a narrative's score
- Doesn't distinguish "1 relevant episode out of 50" from "10 relevant episodes out of 10"
- **Idea**: Maintain a centroid embedding per narrative, updated incrementally as events are added. Use centroid similarity instead of max episode score for ranking. EverMemOS may already have clustering that could serve this purpose — worth investigating.
- **Simpler alternative**: Weighted combination of max + mean top-N scores from existing episode scores

### Problem 4: episode_contents dedup not implemented
- Code extracts `episode_contents` and passes them through the pipeline
- But `_build_short_term_memory_prompt()` never uses them for dedup
- Short-term memory comes from ChatModule DB, episode_contents from EverMemOS — different sources
- **Decision needed**: Either implement the dedup or remove dead code

### Problem 5: Short-term memory loads ALL other narrative messages with no relevance filter
- `_load_short_term_memory()` loads recent 15 messages from ALL other ChatModule instances
- No semantic relevance check — could inject completely unrelated conversations
- Currently bounded by message count (15) and our new 40k char budget
- **Potential improvement**: Score short-term messages against current query embedding and only include above a threshold

### Problem 6: Auxiliary narrative prompt is thin
- Auxiliary narratives only show: name, summary, event count, and max 3 episode summaries (150 chars each)
- The agent has very limited context about auxiliary topics
- **Question**: Should we inject more episode content for the main selected narrative? Currently EverMemOS episodes are ONLY used for routing — the actual context comes entirely from ChatModule chat history. If EverMemOS has richer semantic memory, we're not leveraging it at runtime.

### Problem 7: Event History is disabled
- Part 2 of the system prompt (Event History) is commented out since 2025-12-10
- The system relies entirely on ChatModule for conversation history
- This means narrative-level events (which could contain structured execution traces) are not in the prompt
- **Not necessarily a problem** — ChatModule chat history may be sufficient. But worth noting.

### Problem 8: Participant system only works in a narrow case
- Participants are ONLY added via **Job creation** with `related_entity_id` + `narrative_id`
- `related_entity_id` must be an existing `user_id` in the NarraNexus system
- **Does NOT work when:**
  - Target is an external contact (email, phone) — no user_id in the system
  - Agent communicates via Matrix — Matrix contacts have matrix_user_ids, not NarraNexus user_ids
  - No Job is created — direct messaging or ad-hoc tasks don't create participants
  - SocialNetwork entity_id is used — entity_ids are NOT the same as user_ids, so social graph knowledge doesn't translate to participant routing
- **Example that fails:** "Send a promo email to someone@example.com" → new narrative created, but no participant added, no way to route future context about that person back to this narrative
- **Example that works:** Sales manager creates ONGOING Job targeting "xiaoming_456" (registered user) → Xiaoming added as PARTICIPANT → when Xiaoming talks to the agent, the system routes to the existing narrative
- **Fundamental gap:** Participant routing is limited to registered NarraNexus users linked via explicit Job creation. It doesn't cover inter-agent (Matrix), external contacts, or ad-hoc task scenarios.

### Problem 9: Participant narratives are information-starved in LLM judgment
- LLM only sees `topic_hint[:50]` for name and `topic_hint[:100]` for description
- `narrative_info.name`, `narrative_info.description`, `narrative_info.current_summary` are **completely ignored**
- A rich narrative with 20 turns of context is represented by ~100 chars to the LLM
- Fix: Use `narrative_info` fields (with reasonable truncation) instead of only `topic_hint`

### Problem 9: Search candidate summary truncated to 300 chars
- `narrative_info.current_summary` is truncated to 300 chars for search candidates
- Summaries grow unbounded over conversation turns — a 20-turn narrative could have 3000+ chars
- The LLM misses the full picture when deciding if a query matches
- Root cause: no summary compaction mechanism during narrative updates
- Fix (short-term): Increase limit or use smarter truncation (e.g. keep first + last sentences)
- Fix (long-term): Compact `current_summary` during Step 4.4 narrative update so it stays bounded

### Problem 10: Default/participant matches return zero auxiliary narratives
- When LLM matches "default" or "participant", only 1 narrative is returned
- No auxiliary cross-topic context at all
- "search" matches get up to 2 auxiliary narratives
- This means greeting/casual conversations and participant task contexts have no awareness of related topics

### Problem 11: No limit on narrative summary length
- `current_summary` is included in both the main narrative prompt and continuity detection
- If summaries grow long over many turns, they consume increasing tokens
- **Fix**: Compact/summarize the summary itself during narrative update (Step 4.4), not truncate at read time

---

## Key Files

| File | Purpose |
|------|---------|
| `agent_runtime/_agent_runtime_steps/step_1_select_narrative.py` | Step 1 orchestrator |
| `narrative/narrative_service.py` | Select method — orchestrates retrieval + judgment |
| `narrative/_narrative_impl/retrieval.py` | Vector/EverMemOS retrieval + scoring |
| `narrative/_narrative_impl/_retrieval_llm.py` | LLM judgment (single match + unified match) |
| `narrative/_narrative_impl/continuity.py` | Continuity detection |
| `narrative/_narrative_impl/vector_store.py` | In-memory vector store with filters |
| `narrative/_narrative_impl/prompt_builder.py` | Narrative → system prompt section |
| `narrative/_narrative_impl/default_narratives.py` | 8 default narrative definitions |
| `narrative/session_service.py` | Session management |
| `narrative/config.py` | All thresholds and model configs |
| `utils/evermemos/client.py` | EverMemOS HTTP client |
| `module/memory_module/memory_module.py` | Write-back hook + search interface |
