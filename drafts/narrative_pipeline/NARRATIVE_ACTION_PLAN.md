# Narrative Pipeline — Action Plan

> Last updated: 2026-03-24
> Reference: `NARRATIVE_BIG_PICTURE.md`, `NARRATIVE_RETRIEVAL_PIPELINE.md`

---

## Focus Problems

| ID | Problem | Severity | Pipeline Step |
|----|---------|----------|---------------|
| **R1** | EverMemOS search has no agent_id filter | High | Step 3.4 (Retrieval → EverMemOS search) |
| **R2** | Narrative representation too thin for matching | High | Step 3.6 (score enhancement) + Step 4 (LLM judgment) + Narrative Update Phase 3 (embedding) |
| **R3** | Max-score-per-narrative is brittle ranking | Medium | Step 3.4 (Retrieval → `_aggregate_by_narrative`) |
| **M4** | Episode_contents dedup not implemented | Low | Step 5 (Post-selection → prompt building in `context_runtime.py`) |

## Deferred Problems (discuss with team)

| ID | Problem | Severity | Notes |
|----|---------|----------|-------|
| R4 | Participant routing only works for Job-linked registered users | Medium | Architecture decision — may need broader entity-based routing |
| R5 | `current_summary` grows unbounded | Medium | Needs summary compaction in narrative update |
| R6 | `description` frozen at creation | Low | Minor |
| R7 | Embedding only updates every 5 turns | Low | Usually covered by continuity detection |
| M1 | EverMemOS episodes discarded after routing | ~~High~~ **Not a real problem** | Episodes ARE injected via ChatModule `hook_data_gathering` as long-term memory with `role: "context"`. Verified in `chat_module.py:259-293`. |
| M2 | Short-term memory has no relevance filter | Medium | 15 most recent from all narratives, no scoring |
| M3 | Auxiliary narrative prompt is thin | Medium | Only name + summary + 3 episode summaries at 150 chars |
| M5 | Event History disabled | Low | ChatModule chat history is sufficient for now |
| S1-S4 | Structural / design issues | Design | See BIG_PICTURE.md |
| U1-U6 | Narrative update issues | Various | See RETRIEVAL_PIPELINE.md |

---

## R1: EverMemOS Search Has No Agent-Level Isolation

### Where it appears
- **Step 3.4** — `EverMemOSClient.search_narratives()` in `utils/evermemos/client.py:304-310`
- Search API call: `GET /api/v1/memories/search?user_id={user_id}` — no agent_id param
- Post-filter: `_aggregate_by_narrative()` filters by `agent_narrative_ids` (client-side, after scoring)

### What happens
```
User "hongyi" has Agent A and Agent B
    │
    ▼
EverMemOS search(user_id="hongyi")
    │
    ▼
Returns episodes from BOTH agents (server doesn't know which agent is asking)
    │
    ▼
RRF scoring happens server-side on ALL episodes → ranking polluted
    │
    ▼
Client receives polluted ranking → post-filters by agent_narrative_ids
    │
    ▼
top_k * 3 hack tries to compensate, but:
  - If Agent A has 20 narratives and Agent B has 5, Agent A's results could push
    Agent B's real matches below the 3x cutoff
  - Server-side RRF ranking already decided the order — filtering doesn't fix it
```

### Impact
- Wrong narrative selected when user has multiple agents
- Ranking quality degrades as user accumulates more agents
- `top_k * 3` is a band-aid that doesn't scale

### Proposed fix: Add agent_id as a first-class field in EverMemOS (same as user_id)

**Approach:** `agent_id` should work identically to `user_id` — stored on every message, indexed in all three backends (MongoDB, Elasticsearch, Milvus), filterable at index level on search/fetch/delete.

**Why this works:** EverMemOS already indexes `user_id` as a top-level filterable field at the database/index level (not post-retrieval). `agent_id` follows the exact same pattern. We have the EverMemOS source code.

**Why alternatives don't work:**
- Tags are NOT queryable/filterable in the search API (only organizational metadata)
- `group_id` filter only accepts a single value, not an array
- Encoding agent_id into `sender` or `group_id` would break existing filtering

### EverMemOS changes (we own the source, ~25 files)

| Layer | Files | Change |
|-------|-------|--------|
| **API DTOs** | `api_specs/dtos/memory.py` (1 file) | Add `agent_id` field on write, search, fetch, delete request models |
| **API Controller** | `infra_layer/adapters/input/api/memory/memory_controller.py` (1 file) | Pass `agent_id` through to service layer |
| **Business Layer** | `biz_layer/mem_memorize.py` (1 file) | Extract `agent_id` from messages, propagate through MemCell → Episode |
| **Memory Models** | `api_specs/memory_types.py` (1 file) | Add `agent_id` to MemCell + BaseMemory dataclasses |
| **MongoDB** | document + 2 repos (3 files) | Add indexed `agent_id` field, query/delete methods |
| **Elasticsearch** | 3 doc models + 3 repos (6 files) | Add `Keyword` field + `Q("term", agent_id=agent_id)` filter |
| **Milvus** | 3 collection schemas + 3 repos (6 files) | Add `VARCHAR` field + `agent_id == "{id}"` filter expression |
| **Converters** | 3 ES + 3 Milvus converters (6 files) | Map `agent_id` during data transformation |
| **Delete Service** | `service/memcell_delete_service.py` (1 file) | Support `agent_id` in combined criteria |

Every change follows the identical pattern as `user_id`. No new architecture.

### NarraNexus changes (our code, 2 files)

| File | Change |
|------|--------|
| `utils/evermemos/client.py` — write | Add `"agent_id": self.agent_id` to message payload |
| `utils/evermemos/client.py` — search | Add `"agent_id": self.agent_id` param; remove `top_k * 3` hack and `_filter_pending_messages_by_agent` / `agent_narrative_ids` client-side post-filter |
| `utils/evermemos/client.py` — delete | Add `"agent_id"` param for agent cleanup |

### Migration for existing data

Existing episodes have no `agent_id` field. If we search with `agent_id` filter, old episodes won't match → **agent loses all historical memory**.

**Option A: Backfill script (recommended)**
- We know `group_id = narrative_id` on every episode
- We know `narrative_id → agent_id` mapping from NarraNexus MySQL (`narratives` table has `agent_id`)
- Migration script: query all episodes from EverMemOS, look up agent_id by group_id, update the field in MongoDB + re-index in ES/Milvus
- After backfill, all data has `agent_id` and filtering works cleanly
- **One-time effort**, can run while system is live (episodes are append-only)

**Option B: Graceful fallback (transition period)**
- Keep client-side `agent_narrative_ids` post-filter as fallback
- New episodes get `agent_id` on write → server-side filter works for new data
- Old episodes: still matched by client-side filter (same as today)
- Remove client-side filter after backfill is complete

**Recommended:** Start with **Option B** (no downtime, no data loss), run **Option A** backfill as a background task, then remove fallback code.

### Verification steps

**Before change — check current state:**
```bash
# Fetch episodes, confirm no agent_id field
curl "http://localhost:1995/api/v1/memories?user_id=hongyitest&memory_type=episodic_memory&limit=3"

# Check conversation metadata (tags should show agent_id)
curl "http://localhost:1995/api/v1/memories/conversation-meta?group_id={narrative_id}"

# Search and inspect returned fields
curl -X GET "http://localhost:1995/api/v1/memories/search" \
  -H "Content-Type: application/json" \
  -d '{"query": "test", "user_id": "hongyitest", "retrieve_method": "rrf", "top_k": 3}'
```

**After EverMemOS change — verify new field:**
```bash
# Send a test message with agent_id, wait for extraction
# Then fetch and confirm agent_id appears on new episode
curl "http://localhost:1995/api/v1/memories?group_id={new_narrative_id}&memory_type=episodic_memory&limit=3"
```

**After backfill — verify old data updated:**
```bash
# Fetch old episodes, confirm agent_id is now populated
curl "http://localhost:1995/api/v1/memories?group_id={old_narrative_id}&memory_type=episodic_memory&limit=3"
```

**Search isolation test:**
```bash
# Create episodes from Agent A and Agent B for same user
# Search with agent_id=A → should only return Agent A's episodes
# Search with agent_id=B → should only return Agent B's episodes
# Search without agent_id → should return both (backward compatible)
```

**Raw database checks (if needed):**
```bash
# MongoDB — see raw document fields
docker exec -it <mongodb-container> mongosh
> use evermemos
> db.episodic_memory.findOne({})

# Elasticsearch — check indexed schema
curl "http://localhost:9200/episodic-memory/_mapping"

# Elasticsearch — sample document
curl "http://localhost:9200/episodic-memory/_search?size=1"
```

---

## R2: Narrative Representation Too Thin for Matching

### Where it appears
Three places where narrative information is compressed/truncated:

**1. Embedding generation (Narrative Update Phase 3)**
- `narrative/_narrative_impl/updater.py:443-464` — `_regenerate_topic_hint()`
- `topic_hint` = `"{name}: {summary}"` truncated to **200 chars** (`SUMMARY_MAX_LENGTH`)
- This 200-char string is the ONLY basis for the embedding vector
- A 50-turn narrative with 3000-char summary → 200-char embedding input

**2. LLM judgment (Step 4)**
- `narrative/_narrative_impl/retrieval.py:717-721` — search candidate description
- `narrative_info.current_summary[:300]` — **300 char** truncation for search candidates
- `narrative/_narrative_impl/retrieval.py:763-764` — participant candidates
- `topic_hint[:50]` for name, `topic_hint[:100]` for description — even thinner
- `narrative/_narrative_impl/retrieval.py:697-700` — matched content
- `episode_summaries` joined, capped at **500 chars**

**3. Score enhancement (Step 3.6)**
- `narrative/_narrative_impl/retrieval.py:590-631` — `_enhance_with_events()`
- Uses recent event embeddings to blend with topic score
- But the topic score itself is based on the 200-char topic_hint embedding

### What happens
```
Narrative created: "Help me set up a Python project"
    │
    ▼  (20 turns later, discussed: FastAPI, Docker, CI/CD, testing, deployment)
    │
Summary: 3000 chars of rich context about the full project
    │
    ▼
topic_hint = "Python project setup: Topic: Python project setup\nKey facts:\n- FastA..."
             └── truncated at 200 chars
    │
    ▼
Embedding represents only "Python project setup + FastAPI" (first 200 chars)
Missing: Docker, CI/CD, testing, deployment context
    │
    ▼
User asks "how should I configure the CI pipeline?"
    │
    ▼
Vector search: low score (embedding doesn't capture CI/CD)
LLM judge: sees 300-char summary, misses deployment context
    │
    ▼
Creates new narrative instead of matching existing one
```

### Impact
- Long-running narratives become harder to match as they grow
- Topic drift within a narrative causes retrieval failures
- LLM judge makes decisions on incomplete information
- More new narratives created than necessary → narrative fragmentation

### Proposed fix options

**Option A: Increase SUMMARY_MAX_LENGTH (quick win)**
- Change `SUMMARY_MAX_LENGTH` from 200 to 500-800 chars in `narrative/config.py:164`
- `topic_hint` and therefore embedding captures more of the summary
- **Trade-off:** Slightly higher embedding API cost per update
- **Effort:** Config change only

**Option B: Increase LLM judge truncation limits (quick win)**
- Search candidate description: 300 → 600-800 chars
- Participant candidates: Use `narrative_info.name` + `narrative_info.current_summary[:300]` instead of only `topic_hint[:50/100]`
- Episode summaries: 500 → 1000 chars
- **Files:** `narrative/_narrative_impl/retrieval.py:717-721, 763-764, 697-700`
- **Effort:** Small code changes

**Option C: Summary compaction (medium effort, fixes root cause)**
- In `_async_llm_update()`, if `current_summary` exceeds threshold (e.g., 1500 chars), instruct the LLM to compact it
- Keeps summaries bounded → topic_hint stays informative within 200-500 chars
- **Files:** `narrative/_narrative_impl/updater.py:238-280`
- **Effort:** Add compaction logic + adjust LLM prompt

**Option D: Multi-vector representation (longer term)**
- Instead of one embedding from topic_hint, maintain multiple embeddings:
  - Topic embedding (from name + keywords)
  - Summary embedding (from current_summary)
  - Recent events embedding (from last N events)
- Search uses a weighted combination
- **Effort:** Significant architecture change

### Recommended approach
**A + B immediately** (config/code changes, 30 min), then **C as follow-up** (bounds the root cause). D is future work.

### Files to change
- `narrative/config.py:164` — `SUMMARY_MAX_LENGTH = 200` → `500`
- `narrative/_narrative_impl/retrieval.py:717-721` — increase summary truncation
- `narrative/_narrative_impl/retrieval.py:763-764` — use narrative_info for participant candidates
- `narrative/_narrative_impl/retrieval.py:697-700` — increase episode summary limit
- `narrative/_narrative_impl/updater.py:238-280` — add summary compaction (Option C)

---

## R3: Max-Score-Per-Narrative is Brittle Ranking

### Where it appears
- **Step 3.4** — `EverMemOSClient._aggregate_by_narrative()` in `utils/evermemos/client.py:486-494`

```python
# Current logic:
max_score = max(float(s) for s in scores if s is not None)
narrative_scores[group_id] = max(narrative_scores.get(group_id, 0.0), max_score)
```

### What happens
```
Narrative A: 50 episodes, 1 happens to match a keyword → max_score = 0.08
Narrative B: 10 episodes, 8 are semantically relevant → max_score = 0.07

After RRF scaling:
  A: 0.08 * 10 = 0.80 (above high-confidence threshold!)
  B: 0.07 * 10 = 0.70 (borderline)

Result: Narrative A wins despite B being a much better overall match
```

### Impact
- False positive matches from single-episode keyword hits
- Narratives with broad, consistent relevance rank lower than ones with one lucky match
- Combined with R2 (thin representation), this means the ranking signal is both sparse AND noisy

### Proposed fix options

**Option A: Weighted max + mean (recommended, easy)**
```python
scores_sorted = sorted(scores, reverse=True)
top_n = scores_sorted[:3]  # Top 3 episode scores
max_score = top_n[0]
mean_top_n = sum(top_n) / len(top_n)
narrative_score = 0.6 * max_score + 0.4 * mean_top_n
```
- Rewards narratives with consistently relevant episodes
- Still gives weight to the best match
- Uses data already available (no new API calls)
- **Effort:** ~10 lines of code change

**Option B: Episode count weighting**
```python
# Bonus for having more relevant episodes
episode_count_bonus = min(len(scores), 5) * 0.01  # max 0.05 bonus
narrative_score = max_score + episode_count_bonus
```
- Simpler than Option A
- Slightly rewards breadth without fully restructuring scoring
- **Effort:** ~5 lines

**Option C: Centroid-based scoring (longer term)**
- Maintain a centroid embedding per narrative (updated incrementally when events are added)
- Compare query embedding to narrative centroid for a holistic similarity score
- EverMemOS clustering may already support this — needs investigation
- **Effort:** Architecture change, depends on EverMemOS capabilities

### Recommended approach

**Short-term (done):** R2 fix (remove truncation limits) already improves the `topic_hint`-based embedding quality.

**Medium-term: Maintain a true centroid per narrative on the NarraNexus side.**

Instead of generating `routing_embedding` from `topic_hint` text, incrementally update it as a running average of event embeddings:

```python
# In updater.py, when a new event is added:
event_embedding = await get_embedding(event_text)
count = narrative.round_counter  # or a dedicated counter
if count <= 1:
    narrative.routing_embedding = event_embedding
else:
    old = np.array(narrative.routing_embedding)
    narrative.routing_embedding = ((old * (count - 1) + event_embedding) / count).tolist()
```

Same math as EverMemOS's `ClusterManager._update_cluster_centroid()`. This gives a holistic representation of the narrative's full semantic content, not a single text snapshot.

**Why not use EverMemOS's existing cluster centroids:**
- EverMemOS does maintain `ClusterState` per `group_id` (= `narrative_id`) with cluster centroids in MongoDB
- But those are **sub-topic clusters within a narrative** (e.g., "FastAPI setup", "Docker deployment" as separate clusters inside one narrative)
- We need a **single narrative-level vector** for ranking narratives against each other
- Maintaining our own centroid on `routing_embedding` is simpler and requires no EverMemOS API changes

**Also consider:** Keep weighted max+mean (Option A) as an interim improvement to the EverMemOS episode scoring while the centroid approach is implemented:

```python
scores_sorted = sorted(scores, reverse=True)[:3]
max_score = scores_sorted[0]
mean_top_n = sum(scores_sorted) / len(scores_sorted)
narrative_score = 0.6 * max_score + 0.4 * mean_top_n
```

### Files to change
- `narrative/_narrative_impl/updater.py` — change `_async_embedding_update` / `check_and_update_embedding` to use incremental centroid instead of text-based embedding
- `utils/evermemos/client.py:486-494` — (interim) replace max-score with weighted max+mean

---

## M4: Episode_Contents Dedup Not Implemented

### Where it appears
- **ChatModule.hook_data_gathering()** in `chat_module.py:220-370`
- Long-term: `episode_contents` from `evermemos_memories[current_narrative_id]` → `role: "context"` messages
- Short-term: `_load_short_term_memory()` from OTHER narrative chat instances → `memory_type: "short_term"` messages
- Both merged into `ctx_data.chat_history`, then separated again in `build_input_for_framework()`

### What happens
```
Turn 1: User asks about "Project X budget" in Narrative A
  → ChatModule saves {user: "what's the budget?", assistant: "budget is $50k"} to DB
  → MemoryModule writes event to EverMemOS
  → EverMemOS processes into episode: "User asked about Project X budget. Budget confirmed at $50k."

Turn 3: User sends query in Narrative B that matches Narrative A
  → EverMemOS retrieves specific episodes for Narrative A → episode_contents injected as long-term
  → ChatModule also loads Narrative A's recent chat messages as short-term memory
  → Same information appears TWICE in different formats
```

### Why narrative-level dedup doesn't work
- EverMemOS returns specific top episodes (e.g., 15 out of 100), not all episodes from a narrative
- Short-term memory loads recent chat messages (up to 15) from the same narrative
- They may overlap partially — some short-term messages correspond to the returned episodes, others don't
- Filtering by narrative_id would either over-exclude (drop non-duplicate short-term messages) or under-exclude (miss actual duplicates)

### Recommended approach: Content-level dedup in hook_data_gathering

**Where:** `ChatModule.hook_data_gathering()` (line 334-348), after loading both long-term and short-term messages, before merging.

**How:**
1. Collect all `episode_contents` from `evermemos_memories` (all narratives, available in `ctx_data.extra_data["evermemos_memories"]`)
2. Build a set of episode text fingerprints (e.g., first 100 chars normalized, or a simple hash)
3. For each short-term message, check if its content substantially overlaps with any episode
4. Skip duplicates

**Data availability at dedup point:**
- `ctx_data.extra_data["evermemos_memories"]` → all episode_contents from retrieval (available since Step 1)
- `long_term_messages` → already loaded from evermemos_memories[current_narrative_id]
- `short_term_messages` → just loaded from `_load_short_term_memory()`
- All three are available in `hook_data_gathering()` before the merge at line 350

**Comparison strategy:**
- Episodes are LLM-summarized narratives ("User discussed X. Agent explained Y.")
- Chat messages are raw turns ("User: tell me about X" / "Assistant: Y is...")
- Exact string match won't work — different formats
- Options:
  - (a) **Substring containment**: check if key phrases from the chat message appear in any episode (cheap, catches most cases)
  - (b) **Embedding similarity**: embed both and compare cosine similarity (accurate, but adds latency + API cost per message)
  - (c) **Normalized overlap**: tokenize both, check token overlap ratio (moderate accuracy, no API cost)
- **Recommended:** Option (a) — extract first 80 chars of chat message content, check if it appears as substring in any episode_content. Fast, no API calls, catches the main duplicates.

### Files to change
- `module/chat_module/chat_module.py:334-348` — add dedup between long_term_messages and short_term_messages before merge
- `module/chat_module/chat_module.py:220` — ensure `evermemos_memories` is accessible for collecting all episode_contents

### Code sketch
```python
# In hook_data_gathering(), after loading both long_term and short_term:

# Collect all episode contents for dedup
all_episode_texts = set()
if evermemos_memories:
    for nar_id, data in evermemos_memories.items():
        for ep in data.get("episode_contents", []):
            # Normalize: take first 80 chars, lowercase, strip whitespace
            all_episode_texts.add(ep[:80].lower().strip())

# Filter short-term messages that overlap with episodes
if all_episode_texts:
    original_count = len(short_term_messages)
    short_term_messages = [
        msg for msg in short_term_messages
        if msg.get("content", "")[:80].lower().strip() not in all_episode_texts
    ]
    deduped = original_count - len(short_term_messages)
    if deduped > 0:
        logger.info(f"ChatModule: Deduped {deduped} short-term messages overlapping with episodes")
```

---

## Implementation Priority

| Order | Problem | Fix | Effort | Impact | Status |
|-------|---------|-----|--------|--------|--------|
| 1 | **R2** (thin representation) | `SUMMARY_MAX_LENGTH` 200→800, remove all LLM judge truncation limits | 30 min | High | **Done** |
| 2 | **R1** (agent isolation) | Add `agent_id` as first-class field in EverMemOS (~25 files) + NarraNexus client (2 files) + backfill migration | 2-3 days | High | Scoped, needs EverMemOS work |
| 3 | **R3** (scoring) | Interim: weighted max+mean. Long-term: incremental centroid on `routing_embedding` | 1 hr / 3 hr | Medium | Approach decided |
| 4 | **M4** (dedup) | Content-level dedup in ChatModule.hook_data_gathering() using substring comparison against episode_contents | 2 hr | Low | Approach decided |
| Follow-up | **R2** continued | Summary compaction in narrative updater (bound current_summary growth) | 3 hr | Medium | TODO |
| Future | **R3** continued | Replace text-based routing_embedding with incremental event centroid | 3 hr | High | Approach documented |
