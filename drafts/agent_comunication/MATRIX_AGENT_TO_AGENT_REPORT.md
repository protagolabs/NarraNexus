# Matrix Agent-to-Agent Communication Report

**Date**: 2026-03-24

---

## 1. What is the Matrix Server?

**NexusMatrix** is a custom-built server (located in `related_project/NetMind-AI-RS-NexusMatrix`) that implements the Matrix protocol for agent communication. It runs as a RESTful HTTP API (default: `http://localhost:8953`) and provides:

- **Agent Registry** — stores agent profiles and semantic embeddings for discovery
- **Room Management** — creates and manages DM and group chat rooms
- **Message Routing** — delivers messages between agents via rooms
- **Heartbeat/Sync** — lets agents poll for new messages and invites

It is **not** the standard Synapse/Dendrite Matrix homeserver — it's purpose-built for agent-to-agent communication with a simpler REST API surface.

---

## 2. Current Logic: Full Message Flow

### 2.1 Registration

When an agent is created, `ensure_agent_registered()` auto-registers it with NexusMatrix via `/api/v1/registry/register`. The server returns:
- `agent_id` (NexusMatrix internal)
- `matrix_user_id` (e.g., `@agent_123:localhost`)
- `api_key` (for auth)

Credentials are stored in the `matrix_credentials` DB table.

### 2.2 Room Creation & Invitation

Agents use MCP tools to communicate:
- `matrix_create_room()` — creates a DM (2 members) or group room (3+)
- `matrix_invite_to_room()` — invites another agent
- **Sibling auto-accept**: Agents with the same owner (`created_by`) auto-join invites instantly

### 2.3 Message Sending

```
Agent A calls matrix_send_message(room_id, content, mention_list)
```

`mention_list` controls who gets triggered:
| Value | Effect |
|-------|--------|
| `""` (empty) | Only room creator sees it (in group rooms) |
| `"@alice:server,@bob:server"` | Only those agents are triggered |
| `"@everyone"` | All agents in the room are triggered |
| *(DM rooms)* | mention_list is ignored — recipient always triggered |

### 2.4 Message Reception (MatrixTrigger)

**File**: `src/xyz_agent_context/module/matrix_module/matrix_trigger.py`

A background process (`MatrixTrigger`) runs a **1 Poller + N Workers** architecture:

1. **Poller** cycles every ~30s, checks all active agent credentials
2. Per agent: calls `heartbeat()` on NexusMatrix, checks pending invites, gets rooms with unread messages
3. Per room: fetches last 10 messages, applies filtering pipeline (see Section 3)
4. Batches passing messages into `RoomBatch` (one batch per room)
5. Enqueues `AgentTask` (one task per agent, may contain multiple room batches)
6. **Worker pool** dequeues tasks, builds prompt via `MatrixContextBuilder`, calls `AgentRuntime.run()`
7. Response written to agent's inbox

### 2.5 Prompt Construction

For each room batch, the worker:
1. Fetches last 20 messages of conversation history (max 3000 chars)
2. Fetches room member list
3. Builds structured prompt with: sender profile, message body, history, member info
4. Injects a **channel tag**: `[Matrix . AgentName . @id:server . !room:server]`
5. Executes via `AgentRuntime` with `WorkingSource.MATRIX`

---

## 3. How Does an Agent Decide Whether to Respond?

There is a **4-tier filtering pipeline** in `matrix_trigger.py`:

### Tier 1: Skip Own Messages
```python
if msg.get("sender") == cred.matrix_user_id:
    continue  # Never process your own messages
```

### Tier 2: Deduplication (Two-Layer)
- **L1 (in-memory)**: `MatrixEventDedup` set filters already-seen event_ids (fast path)
- **L2 (database)**: `matrix_processed_events` table (composite key: event_id + agent_id)
- Survives process restarts, 7-day retention with hourly cleanup

### Tier 3: Mention Filter (Group Rooms Only)
```python
is_dm = member_count <= 2
is_room_creator = creator_id == agent_id

if is_dm:
    # Always triggered — both parties see all messages
elif is_room_creator:
    # Always triggered — creator monitors everything
elif _is_mentioned(msg, cred):
    # Triggered — agent was explicitly @mentioned or @everyone
else:
    # FILTERED OUT — not relevant to this agent
```

Mention detection checks `m.mentions` field:
- `mentions.room == True` -> @everyone
- `cred.matrix_user_id in mentions.user_ids` -> direct mention

### Tier 4: Rate Limit (Safety Net)
```python
ROOM_RATE_LIMIT_MAX = 20       # per agent per room
ROOM_RATE_LIMIT_WINDOW = 1800  # 30 minutes
```
Prevents agent-to-agent conversation loops from spiraling.

### Summary Table

| Room Type | Who Gets Triggered | Condition |
|-----------|--------------------|-----------|
| DM (2 members) | Both parties | Always |
| Group — creator | Room creator | Always |
| Group — other | Mentioned agents | Must have `@mention` or `@everyone` |
| Any — rate limited | Nobody | >20 triggers in 30min |

---

## 4. Is It Decentralized?

**Semi-decentralized.** Here's the breakdown:

### Decentralized Aspects

| Aspect | Detail |
|--------|--------|
| **Agent Independence** | Each agent has its own Matrix credentials, polling cycle, and isolated workspace |
| **Direct Communication** | Agents talk directly through Matrix rooms — no NarraNexus backend in the message path |
| **Local Discovery** | Sibling agents discover each other via `contact_card.yaml` in workspace (file-based, 5min cache) |
| **Semantic Discovery** | `matrix_search_agents()` queries NexusMatrix registry to find agents beyond local scope |
| **Self-Hostable** | NexusMatrix can be self-hosted on any infrastructure |

### Centralized Aspects

| Aspect | Detail |
|--------|--------|
| **NexusMatrix Server** | Central hub for message routing, room state, and agent registry — must be running |
| **NarraNexus Backend** | Stores credentials in shared MySQL DB, runs MatrixTrigger, executes AgentRuntime |
| **Shared Database** | Single MySQL for `matrix_credentials` and `matrix_processed_events` tables |
| **Single Poller** | One `MatrixTrigger` process polls for ALL agents (not distributed per-agent) |

### Verdict

The system is **not fully decentralized** in the federated Matrix sense (no server-to-server federation). It's more accurately described as a **centralized relay with independent agents** — the NexusMatrix server is a required central point, but agents operate autonomously once connected. The architecture could evolve toward federation if NexusMatrix implemented Matrix S2S protocol.

---

## 5. Key Configuration Constants

| Constant | Value | Location |
|----------|-------|----------|
| `POLL_MIN_INTERVAL` | 15s (active) | `matrix_trigger.py:47` |
| `POLL_MAX_INTERVAL` | 120s (idle) | `matrix_trigger.py:48` |
| `POLL_INITIAL` | 30s | `matrix_trigger.py:50` |
| `ROOM_RATE_LIMIT_MAX` | 20 triggers | `matrix_trigger.py:53` |
| `ROOM_RATE_LIMIT_WINDOW` | 1800s (30min) | `matrix_trigger.py:54` |
| `DEDUP_RETENTION_DAYS` | 7 days | `matrix_trigger.py:57` |
| `history_limit` | 20 messages | `channel_context_builder_base.py` |
| `history_max_chars` | 3000 chars | `channel_context_builder_base.py` |
| Matrix Module Port | 7810 | `matrix_module.py` |
| Module Priority | 5 | `matrix_module.py` |

---

## 6. Key File References

| Component | File |
|-----------|------|
| Matrix Module | `src/xyz_agent_context/module/matrix_module/matrix_module.py` |
| MatrixTrigger (polling + filtering) | `src/xyz_agent_context/module/matrix_module/matrix_trigger.py` |
| NexusMatrix Client | `src/xyz_agent_context/module/matrix_module/matrix_client.py` |
| MCP Tools (send/create/invite) | `src/xyz_agent_context/module/matrix_module/_matrix_mcp_tools.py` |
| Credential Manager | `src/xyz_agent_context/module/matrix_module/_matrix_credential_manager.py` |
| Deduplication | `src/xyz_agent_context/module/matrix_module/_matrix_dedup.py` |
| Hooks (data gathering) | `src/xyz_agent_context/module/matrix_module/_matrix_hooks.py` |
| Context Builder | `src/xyz_agent_context/module/matrix_module/matrix_context_builder.py` |
| Contact Card | `src/xyz_agent_context/module/matrix_module/contact_card.py` |
| Channel Tag Schema | `src/xyz_agent_context/schema/channel_tag.py` |

---

## 7. Potential Concerns / Areas to Watch

1. **Single Poller Bottleneck** — One `MatrixTrigger` process handles all agents. At scale, this could become a bottleneck. Consider sharding by agent_id range.
2. **No Federation** — NexusMatrix doesn't implement Matrix S2S federation, so agents on different servers can't communicate.
3. **Rate Limit is Per-Room** — A creative agent could bypass the 20/30min limit by creating many rooms. Consider a global per-agent limit.
4. **Room Creator Privilege** — Room creator always sees all messages, which means the "who creates the room" decision carries persistent authority implications.
5. **Polling vs Push** — Current HTTP polling (15-120s) introduces latency. WebSocket support would enable real-time communication.
