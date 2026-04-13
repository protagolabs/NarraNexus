# Narrative Pipeline — Big Picture & Problem Analysis

> Last updated: 2026-03-24
> Companion doc: `NARRATIVE_RETRIEVAL_PIPELINE.md` (detailed step-by-step)

---

## 1. The Big Picture

### What we're trying to do

An agent has many conversations across many topics over time. When a user sends a new message, we need to:

1. **Route** — find the right topic-level narrative(s) that this message belongs to (or create a new one)
2. **Recall** — bring relevant past context into the agent's working memory so it can respond with awareness of what happened before

These are two distinct jobs that currently share the same pipeline.

### Why it matters

- **Wrong routing** → agent confuses topics, loses track of projects, mixes up customers
- **Weak recall** → agent forgets what was discussed 3 sessions ago, repeats questions, misses connections across topics
- **Both failing** → the agent feels like it has amnesia, even though the data exists somewhere in the system

---

## 2. Related Data Structures

### Narrative (NarraNexus, MySQL)

The **topic container**. Does NOT store conversation content — only routing metadata and orchestration config.

```
Narrative
├── Identity: id, type (CHAT/TASK/OTHER), agent_id
├── Core Content (NarrativeInfo):
│   ├── name              ← LLM-generated, updated every turn
│   ├── description       ← frozen at creation, never updated
│   ├── current_summary   ← LLM-generated, updated every turn, UNBOUNDED
│   └── actors[]          ← USER (owner), AGENT, PARTICIPANT (Job-linked only)
├── Routing Index:
│   ├── routing_embedding ← vector, updated every 5 turns
│   ├── topic_hint        ← "name: summary" truncated to 200 chars, basis for embedding
│   └── topic_keywords[]  ← LLM-generated, updated every turn
├── Orchestration:
│   ├── active_instances[] ← module instances currently active
│   ├── event_ids[]        ← all events (chronological)
│   └── dynamic_summary[]  ← per-event one-line summaries
├── Metadata: created_at, updated_at, round_counter
└── Special: is_special ("default" for 8 built-in types, "other" for user-created)
```

**Key characteristics:**
- Has `agent_id` but NO `user_id` — ownership tracked via `actors` list
- One narrative per topic per agent (not per user)
- PARTICIPANT actors only added via Job creation, not ad-hoc
- `description` frozen forever, `current_summary` grows unbounded

### EverMemOS Data Model (External Service, MongoDB + Milvus + Elasticsearch)

EverMemOS processes raw conversation messages into structured memories:

```
Raw Messages (from NarraNexus)
    │
    ▼
MemCell (atomic memory unit, boundary-detected topic chunk)
    │
    ├── Group Episode     ← third-person narrative summary of the MemCell
    │                       (user_id = None, shared across all participants)
    ├── Personal Episode  ← first-person view per participant
    │                       (user_id = specific user)
    ├── Foresight         ← predictive memories ("this may affect X next week")
    │                       (assistant scene only)
    └── EventLog          ← structured event records
```

**How NarraNexus maps to EverMemOS:**

| NarraNexus concept | EverMemOS concept | Link key |
|--------------------|-------------------|----------|
| Narrative | Conversation Group | `group_id = narrative_id` |
| Event (user input + agent response) | 2 Messages (user + assistant) | `message_id = event_id + _user/_agent` |
| Agent | Part of `sender_name` field | No explicit agent field on messages |
| User | `sender` field + search `user_id` param | User-level isolation on search API |

**Episode structure** (what comes back from search):
```json
{
  "summary": "Short summary of the episode",
  "episode": "Full narrative text of the episode",
  "participants": ["user_123", "agent_abc"],
  "memcell_event_id_list": ["evt_1", "evt_2"]
}
```

### ChatModule Memory (NarraNexus, MySQL)

The **conversation transcript**. Stored per ChatModule instance (one per user per narrative).

```
instance_json_format_memory_chat
├── instance_id (= chat_xxxxxxxx, one per user per narrative)
└── messages[]
    ├── {role: "user", content: "...", meta_data: {timestamp, working_source, ...}}
    └── {role: "assistant", content: "...", meta_data: {timestamp, ...}}
```

**Key characteristics:**
- One chat instance per `(user_id, narrative_id)`
- Contains raw message pairs (user input + agent response)
- This is what becomes long-term memory in the prompt
- Short-term memory = messages from OTHER narratives' chat instances

### Session (NarraNexus, JSON file)

Tracks continuous conversation state per `(user_id, agent_id)`:
```
{agent_id}_{user_id}.json
├── current_narrative_id  ← which narrative we're in
├── last_query, last_response, last_query_embedding
├── last_query_time       ← for timeout (600s)
└── query_count
```

---

## 3. Current Narrative Retrieval Pipeline (Summary)

```
User message arrives
    │
    ▼
[Session] → Do we have an active session with a current narrative?
    │
    ▼
[Continuity] → LLM: does this message belong to the current narrative?
    │
    ├── YES → use current narrative + fetch auxiliary Top-K
    │
    ▼── NO
[Retrieval] → Search for candidate narratives
    │
    │   EverMemOS path: search episodes by user_id → aggregate by group_id (=narrative_id)
    │                   → post-filter by agent_narrative_ids → RRF score scaling
    │   Vector path (fallback): search by embedding with {user_id, agent_id} filters
    │
    ▼
[Judgment] → Score >= 0.70? Direct match. Otherwise LLM picks from:
    │         PARTICIPANT narratives + DEFAULT narratives + SEARCH results
    │
    ▼
[Selection] → 1 main narrative + 0-2 auxiliary narratives
    │
    ▼
[Prompt Building]
    System prompt: narrative metadata + module instructions + auxiliary summaries
    Conversation: ChatModule long-term history (current narrative)
    Appendix: short-term memory (recent 15 msgs from OTHER narratives)
```

---

## 4. Problems & What They Break in the Big Picture

### Routing Problems — "We pick the wrong narrative"

| # | Problem | Severity | What it breaks |
|---|---------|----------|----------------|
| **R1** | EverMemOS search has no agent_id filter — returns episodes from all agents serving the same user | **High** | Agent A's narratives pollute Agent B's ranking. A user with 10 agents gets wrong routing. Root cause of the `top_k * 3` hack. |
| **R2** | Narrative representation is too thin for matching — embedding based on 200-char `topic_hint`, LLM judge sees 300-char summary | **High** | A rich 50-turn narrative is represented by ~200 chars for routing. LLM judge makes decisions on incomplete information. Long-running narratives become harder to match as their summary outgrows the truncation. |
| **R3** | Max-score-per-narrative ranking — one lucky episode match can inflate a narrative's score | **Medium** | Narrative with 1 relevant episode out of 50 ranks the same as one with 10 out of 10. Leads to false positive matches. |
| **R4** | Participant routing only works for registered NarraNexus users linked via Jobs | **Medium** | Matrix contacts, external emails/phones, SocialNetwork entities — none of these can be participants. The whole PARTICIPANT system is limited to a narrow internal use case. |
| **R5** | `current_summary` grows unbounded — no compaction | **Medium** | Summary feeds back into itself on every LLM update. Over many turns, becomes noisy and expensive. Also inflates the system prompt and continuity detection input. |
| **R6** | `description` frozen at creation, never updated | **Low** | Stale description in LLM judge context. Minor since `current_summary` is preferred. |
| **R7** | Embedding only updates every 5 turns | **Low** | After a topic shift within a narrative, vector search is stale for up to 4 turns. Usually covered by continuity detection. |

### Recall Problems — "We have the data but the agent doesn't see it"

| # | Problem | Severity | What it breaks |
|---|---------|----------|----------------|
| **M1** | EverMemOS episodes are discarded after routing — never enter the prompt | **High** | EverMemOS is the only semantic memory retrieval system. It finds relevant past episodes, but the agent never sees them. The agent's actual context comes entirely from ChatModule chat history (current narrative only) + thin auxiliary summaries. Cross-narrative semantic recall is effectively broken. |
| **M2** | Short-term memory has no relevance filter — loads 15 most recent messages from all other narratives | **Medium** | Injects potentially unrelated conversations as context. Wastes tokens. Could confuse the agent when topics are diverse. |
| **M3** | Auxiliary narrative prompt is thin — name + summary + max 3 episode summaries at 150 chars each | **Medium** | Auxiliary narratives provide almost no useful context. An auxiliary narrative about "Project X" contributes ~500 chars total. The agent can't meaningfully use this for cross-topic reasoning. |
| **M4** | Episode_contents dedup not implemented — code extracts data but never uses it | **Low** | Dead code path. Short-term memory and EverMemOS episodes could overlap, showing duplicate content. Not impactful until M1 is fixed (episodes don't enter prompt today). |
| **M5** | Event History disabled in prompt (commented out since Dec 2025) | **Low** | Agent has no visibility into its own past reasoning or tool usage. Only sees final responses. Not necessarily a problem if ChatModule history is sufficient, but limits self-reflection. |

### Structural Problems — "The architecture has gaps"

| # | Problem | Severity | What it breaks |
|---|---------|----------|----------------|
| **S1** | Two separate memory systems (ChatModule DB vs EverMemOS) with different data, no coordination | **Design** | ChatModule stores raw messages in MySQL. EverMemOS processes them into semantic episodes in MongoDB. They're written to independently. The prompt uses ChatModule for conversation and EverMemOS only for routing. Neither system knows about the other's state. |
| **S2** | Narrative has no user_id field — ownership only via actors list | **Design** | Can't do simple `WHERE user_id = ?` queries. Participant queries need JSON parsing. Makes multi-user scenarios more complex than necessary. |
| **S3** | No cross-narrative memory in the prompt beyond thin short-term memory | **Design** | If you discussed "Project X budget = $50k" in narrative A three weeks ago, and now you're in narrative B asking "what's our budget?", the agent has no way to recall it. Short-term memory only covers recent messages (15 max), EverMemOS episodes are discarded after routing. |
| **S4** | LLM update runs on every turn (NARRATIVE_LLM_UPDATE_INTERVAL=1) for main narrative | **Design** | Every single message triggers a gpt-4o-mini call to update name, summary, keywords. This is a cost/latency decision — the benefit is always-fresh metadata, the cost is ~$0.001 per message. Could be reduced to every 3-5 turns with minimal quality loss. |

---

## 5. The Core Tension

The pipeline tries to do two things:

1. **Routing** — find the right narrative (needs: fast, coarse-grained topic matching)
2. **Recall** — bring relevant past context into the prompt (needs: rich, fine-grained semantic retrieval)

Currently, EverMemOS is used for #1 (routing) but its output is thrown away before #2 (recall). The result is:

- **Routing works reasonably well** — between continuity detection, vector search, and LLM judgment, the right narrative is usually found
- **Recall is weak** — the agent operates primarily on ChatModule chat history from the current narrative, with minimal cross-narrative awareness

**The biggest opportunity** is using EverMemOS for what it's actually designed for — semantic memory retrieval and injection — not just as a routing signal. The episodes it retrieves contain exactly the kind of cross-conversation context the agent needs.

---

## 6. Key Questions for Design Decisions

1. **Should EverMemOS episodes be injected into the prompt for the selected narrative?** This would give the agent semantic memory from past conversations. Trade-off: token cost vs recall quality.

2. **Should we fix EverMemOS agent isolation at the API level or keep the client-side filter?** API-level fix is cleaner but requires EverMemOS changes. Client-side filter works but wastes bandwidth and risks ranking pollution.

3. **Should we compact narrative summaries?** Unbounded growth is causing cascading problems (embedding quality, LLM judge accuracy, token cost). Compaction on every N turns or when exceeding a threshold would bound the problem.

4. **Should we rethink the participant system?** Current design only works for Job-linked registered users. Matrix contacts, external contacts, and ad-hoc scenarios are unsupported. Is the PARTICIPANT concept worth keeping, or should it be replaced with something broader (e.g., entity-based routing via SocialNetwork)?

5. **Should short-term memory be relevance-filtered?** Current approach (15 most recent from all other narratives) is simple but noisy. Scoring against query embedding would improve quality but add latency.

6. **What should the narrative centroid look like?** Current: 200-char topic_hint embedding. Options: maintain a running centroid from all event embeddings, use EverMemOS clustering, or increase topic_hint length.
