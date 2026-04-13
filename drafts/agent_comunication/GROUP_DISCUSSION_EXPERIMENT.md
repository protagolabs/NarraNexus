# Agent Group Discussion Experiment — Feasibility Assessment

> Date: 2026-03-31

## Objective

Create a 4-agent group with different personas to engage in a group discussion, filling out a Business Model Canvas for the "Reinventing NovaTech" circular electronics challenge. A controller agent orchestrates the session.

---

## Task Background

The NovaTech challenge requires a team to collaboratively design a circular business model canvas (9 components) for a mid-sized European consumer electronics company (£850M revenue). The team must transform from linear sell-use-dispose to circular reuse-repair-renew model.

---

## Feasibility Assessment

### Q1: How many messages can an agent see?

| Context | Limit | Notes |
|---------|-------|-------|
| **Trigger polling** (what triggers a response) | Last **10** messages fetched per room | `matrix_trigger.py:541` |
| **Conversation history in prompt** | Last **20** messages, max **3000 chars** | `channel_context_builder_base.py` — configurable via `ChannelHistoryConfig` |
| **MCP tool `matrix_get_messages()`** | Default **20** messages | Agent can call this programmatically to read more |
| **Short-term memory** (cross-narrative) | Last **15** messages | `chat_module.py:72` — `SHORT_TERM_MAX_MESSAGES = 15` |
| **Single message max chars** | **4000** chars | `context_runtime.py:57` — longer messages truncated |

**Bottom line:** Each agent sees the last ~20 messages of room history when responding. In a fast-moving 4-agent discussion, this means an agent could "forget" earlier messages if the conversation exceeds 20 exchanges quickly.

### Q2: Does short-term and long-term memory apply to group chat?

**Short-term memory (ChatModule):**
- YES, it applies. The `SHORT_TERM_MAX_MESSAGES = 15` most recent messages are injected into context regardless of source (user chat or Matrix).
- However, this is per-agent, cross-narrative. So Matrix messages DO contribute to short-term memory.

**Long-term memory (EverMemOS / MemoryModule):**
- YES, the MemoryModule `hook_after_event_execution()` writes conversation events to EverMemOS after each agent response.
- On next trigger, `hook_data_gathering()` does semantic search against EverMemOS to retrieve relevant past context.
- Limits: `MAX_SEMANTIC_MEMORY_CHARS = 1500`, `EVERMEMOS_SEARCH_TOP_K = 10`
- This means agents can recall relevant past discussion points even beyond the 20-message window.

**Caveat:** Memory quality depends on whether EverMemOS is enabled and configured for these agents.

### Q3: Is there a hard limit for group chat?

| Limit | Value | Impact |
|-------|-------|--------|
| **Max room members** | **None** (no hard limit in code) | 4 agents + controller = 5 is fine |
| **Rate limit** | **20 triggers per agent per room per 30 min** | With 4 agents, if each speaks ~5 times per 30 min, you're fine. But a fast back-and-forth could hit this. |
| **Polling interval** | **15s (active) to 120s (idle)** | Minimum ~15s between an agent seeing a new message and responding. Group discussion will be slow-paced (not real-time). |
| **Room creator privilege** | Creator sees ALL messages | The controller agent should create the room so it monitors everything. |

**Key concern:** The **rate limit of 20 triggers/30min/agent/room** is the most relevant constraint. In a 4-agent group discussion aiming for a rich conversation, each agent can speak at most ~20 times in 30 minutes. That's 80 total messages per 30-minute window — should be sufficient for a structured discussion.

### Q4: Can we extract conversations and present them nicely?

**YES, multiple approaches:**

1. **Matrix Client API** — `GET /api/v1/messages/{room_id}/history` with pagination support (`since` token). Returns structured JSON with sender, timestamp, body, event_id.

2. **MCP Tool** — `matrix_get_messages(agent_id, room_id, limit=20)` — agents can read their own room history.

3. **Agent Trajectory Files** — Each agent response generates a trajectory record (Step 4.1) stored on disk. Contains full execution trace including input, reasoning, tool calls, and output.

4. **Database Events** — Each agent response creates an Event record with `final_output`, linked to narratives.

**Recommended extraction approach:**
- Use the Matrix Client API directly to pull full room history (paginated, unlimited).
- Parse into a structured format (JSON → Markdown/HTML).
- Could build a simple script that calls `GET /api/v1/messages/{room_id}/history` repeatedly with pagination tokens to get the complete conversation.

---

## Architecture for the Experiment

```
Controller Agent (room creator)
    │
    ├── Creates group room
    ├── Invites 4 persona agents
    ├── Sends initial brief + BMC template
    ├── Uses @everyone to trigger all agents
    │
    ▼
┌─────────────────────────────────────────┐
│            Group Chat Room              │
│                                         │
│  Agent A: CEO / Strategy Lead           │
│  Agent B: Sustainability Expert         │
│  Agent C: Finance / Revenue Analyst     │
│  Agent D: Customer Experience Designer  │
│                                         │
│  Discussion flow:                       │
│  1. Controller posts task + context     │
│  2. Each agent gives initial take       │
│  3. Agents @mention each other or       │
│     @everyone for follow-ups            │
│  4. Controller nudges to fill BMC gaps  │
│  5. Controller calls for final canvas   │
└─────────────────────────────────────────┘
    │
    ▼
Extract via Matrix API → Format as report
```

---

## Potential Issues & Mitigations

| Issue | Risk | Mitigation |
|-------|------|------------|
| **20-message history window** | Agents lose early context in long discussions | Use EverMemOS for recall; controller can re-summarize periodically |
| **3000 char history limit** | Long messages eat up history space | Keep agent messages concise in awareness/persona |
| **15s minimum polling** | Discussion is slow (~15-30s per turn minimum) | Accept async pace; plan for ~30-60 min total session |
| **Rate limit (20/30min)** | Could hit limit in fast exchanges | Monitor; structure discussion in phases |
| **Mention routing** | Agents only respond if mentioned (except creator) | Use `@everyone` for broadcast; explicit `@agent` for directed questions |
| **Conversation loops** | Agents keep responding to each other endlessly | Rate limit is the safety net; controller can intervene |

---

## Data Storage & Extraction

### Where messages are stored

Messages are **NOT** in the NexusMatrix DB (`related_project/NetMind-AI-RS-NexusMatrix/data/nexus_matrix.db`) — that only has `agents`, `api_keys`, `feedback`, `sync_tokens` tables.

Messages are in the **Synapse homeserver SQLite database**:
- **Path:** `deploy/synapse/data/homeserver.db`
- **Tables:** `events` (metadata, sender, timestamps) + `event_json` (full JSON payload including message body)
- **Room ID:** `!naNYhNXTvYciNWgjRL:localhost` (NovaTech BMC Workshop)

### Agent session files

Per-agent context is also stored in `sessions/`:
| Agent | File |
|-------|------|
| Sam | `sessions/agent_927887cf84f9_hongyitest.json` |
| Maya | `sessions/agent_6838ac5c3fb5_hongyitest.json` |
| Alex | `sessions/agent_902b10ce566e_hongyitest.json` |
| Jordan | `sessions/agent_d001b00f3329_hongyitest.json` |
| Facilitator | `sessions/agent_f7f2718714fb_hongyitest.json` |

### Extraction method (SQL → Markdown)

```sql
-- Get messages from a specific room
SELECT 
  e.sender,
  datetime(e.origin_server_ts/1000, 'unixepoch', 'localtime') as timestamp,
  ej.json
FROM events e
JOIN event_json ej ON e.event_id = ej.event_id
WHERE e.room_id = '!naNYhNXTvYciNWgjRL:localhost'
  AND e.type = 'm.room.message'
ORDER BY e.origin_server_ts ASC;

-- Get display names for sender mapping
SELECT e.sender, ej.json
FROM events e
JOIN event_json ej ON e.event_id = ej.event_id
WHERE e.room_id = '!naNYhNXTvYciNWgjRL:localhost'
  AND e.type = 'm.room.member';
```

Message body is inside `json` column → parse as JSON → `content.body`.
Display names are in member events → `content.displayname`.

### Extracted transcripts

| Run | Room | Messages | File |
|-----|------|----------|------|
| Run 1 (stopped early) | `!naNYhNXTvYciNWgjRL:localhost` | 11 | `NOVATECH_DISCUSSION_TRANSCRIPT.md` |
| Run 2 (Circular DaaS) | `!KtZMfyggEJFSuhknSg:localhost` | 65 | `NOVATECH_DAAS_RAW_TRANSCRIPT.md` (raw) |
| | | | `NOVATECH_DAAS_DISCUSSION.md` (sectioned, shareable) |

---

## Configuration Changes Applied

| Change | File | Old | New | Reason |
|--------|------|-----|-----|--------|
| `history_max_chars` | `channel_context_builder_base.py:52` | 3000 | 20000 | Allow agents to see more conversation history in group discussions |

---

## Next Steps

1. ~~Define 4 agent personas (awareness text)~~ DONE — see `EXPERIMENT_AGENT_PROMPTS.md`
2. ~~Create agents in the system~~ DONE
3. ~~Run pilot discussion~~ IN PROGRESS — 11 messages so far
4. Build extraction script for post-discussion analysis — DONE (SQL extraction method documented)
5. Evaluate discussion quality and BMC completeness
6. Iterate on agent prompts if needed
