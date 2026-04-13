# Bootstrap Configuration — Working Logic

> How the first-run setup works end-to-end, including every condition and decision path.

---

## 1. Lifecycle Overview — Detailed Flow

```
╔══════════════════════════════════════════════════════════════════════════════╗
║  STAGE 1: AGENT CREATION  (backend/routes/auth.py → POST /api/auth/agents) ║
╚══════════════════════════════════════════════════════════════════════════════╝

  User clicks "Create Agent" in Sidebar
       │
       ▼
  Backend: create_agent()
       │
       ├── 1. Validate user exists in DB
       ├── 2. Generate agent_id = "agent_{uuid_hex[:12]}"
       ├── 3. Insert into `agents` table (name="New Agent", created_by=user_id)
       │
       ▼
  Eager workspace + Bootstrap.md write (best-effort, non-fatal)
       │
       ├── 4. Build path: {base_working_path}/{agent_id}_{created_by}/
       │       e.g. agent_workspace/agent_a410d87ccaf4_hongyitest/
       ├── 5. os.makedirs(path, exist_ok=True)
       └── 6. Write BOOTSTRAP_MD_TEMPLATE → {path}/Bootstrap.md
                │
                ▼
  Frontend receives response → adds "New Agent" to sidebar


╔══════════════════════════════════════════════════════════════════════════════╗
║  STAGE 2: USER SENDS A MESSAGE  (WebSocket → agent_runtime)               ║
╚══════════════════════════════════════════════════════════════════════════════╝

  User types message → WebSocket sends to backend
       │
       ▼
  Agent Runtime pipeline begins (step_0 → step_3):
       │
       ├── step_0: Initialize — fetch agent record from DB
       ├── step_1: Narrative retrieval — find/create narrative for this user
       ├── step_1.5: Load markdown history
       ├── step_2: Module loading — LLM instance decision selects active modules
       │           (AwarenessModule, ChatModule, JobModule, SkillModule, etc.)
       │           NOTE: BasicInfoModule may NOT be loaded here
       │
       ▼
  step_3: Context building — ContextRuntime.run()
       │
       ├── 3a. hook_data_gathering() — each module enriches ContextData
       │        (awareness, chat history, jobs, skills, etc.)
       │
       ├── 3b. build_complete_system_prompt()
       │        │
       │        ├── Part 1: Narrative Info (main narrative)
       │        ├── Part 2: Event History (currently disabled, ChatModule provides)
       │        ├── Part 3: Auxiliary Narratives (related topic summaries)
       │        ├── Part 4: Module Instructions (awareness, chat, job, skill, ...)
       │        │
       │        └── Part 5: *** BOOTSTRAP INJECTION CHECK ***
       │                │
       │                ▼
       │     ┌──────────────────────────────────────────────────┐
       │     │  Query DB: AgentRepository.get_agent(agent_id)   │
       │     │                                                  │
       │     │  agent.created_by == ctx_data.user_id ?          │
       │     │       │                    │                     │
       │     │      YES                   NO ──────► SKIP       │
       │     │       │                    (not the creator,     │
       │     │       ▼                     no bootstrap)        │
       │     │  os.path.isfile(                                 │
       │     │    {workspace}/Bootstrap.md                      │
       │     │  ) ?                                             │
       │     │       │                    │                     │
       │     │      YES                   NO ──────► SKIP       │
       │     │       │                    (already completed,   │
       │     │       ▼                     file was deleted)    │
       │     │  Read Bootstrap.md content                       │
       │     │  Wrap in BOOTSTRAP_INJECTION_PROMPT              │
       │     │  Append to system prompt as Part 5               │
       │     │                                                  │
       │     │  Result: system prompt now contains:             │
       │     │  "⚡ Bootstrap Mode (PRIORITY)                   │
       │     │   You are in first-run bootstrap mode.           │
       │     │   This takes priority over all other             │
       │     │   instructions. ..."                             │
       │     └──────────────────────────────────────────────────┘
       │
       ├── 3c. build_input_for_framework()
       │        Combine: system prompt + chat history + current user input
       │        Collect MCP URLs from active modules
       │
       ▼
  step_3 continued: Agent Loop (Claude SDK)
       │
       Agent receives the full system prompt (with or without bootstrap)
       Agent generates response


╔══════════════════════════════════════════════════════════════════════════════╗
║  STAGE 3: BOOTSTRAP CONVERSATION  (agent-driven, guided by template)      ║
╚══════════════════════════════════════════════════════════════════════════════╝

  Because bootstrap is injected, the agent follows the template:
       │
       ▼
  Phase 1: Identity & Creator
       │
       Agent: "Hey — I just came online. What should I call myself?
               And who are you?"
       │
       ├── Creator gives both name + their name ──► proceed to Phase 2
       ├── Creator gives only agent name ──► agent asks for creator name
       ├── Creator gives only their name ──► agent asks for agent name
       └── Creator says something off-topic ──► agent tries to collect
                                                 what it can, moves on
       │
       ▼
  Phase 2: Personality & Purpose (optional)
       │
       Agent: "Got it — I'm [name]. Want to tell me how I should
               behave, what I'm for, or how I should talk?"
       │
       ├── Creator gives guidance ──► agent acknowledges warmly
       └── Creator says skip/no/later ──► agent wraps up


╔══════════════════════════════════════════════════════════════════════════════╗
║  STAGE 4: TOOL CALLS  (agent calls MCP tools on AwarenessModule, port 7801)║
╚══════════════════════════════════════════════════════════════════════════════╝

  Once agent has collected at least the agent name:
       │
       ▼
  Tool 1: update_agent_name(agent_id, "chosen_name")
       │
       ├── awareness_module.py → AgentRepository.update_agent()
       ├── UPDATE agents SET agent_name = 'chosen_name' WHERE agent_id = ...
       └── DB now has the real name (was "New Agent", now "chosen_name")
       │
       ▼
  Tool 2: update_awareness(agent_id, full_markdown_profile)
       │
       ├── awareness_module.py → InstanceAwarenessRepository.upsert()
       ├── Writes 4-section Markdown awareness profile:
       │     Section 1: Narrative Management — "No observations yet"
       │     Section 2: Task Decomposition — "No observations yet"
       │     Section 3: Communication Style — filled with personality notes
       │     Section 4: Role and Identity — filled with name + creator info
       └── This profile is injected into every future system prompt
       │
       ▼
  Tool 3: Delete Bootstrap.md (via Claude SDK built-in file tools)
       │
       ├── File path: agent_workspace/{agent_id}_{created_by}/Bootstrap.md
       ├── This is the agent's CWD (set at step_3_agent_loop.py:149)
       └── Agent can delete files in its own workspace
       │
       ▼
  Agent confirms to creator:
       "All set! I'm [name] now. Talk to me anytime."


╔══════════════════════════════════════════════════════════════════════════════╗
║  STAGE 5: FRONTEND REFRESH  (ChatPanel → configStore → Sidebar)           ║
╚══════════════════════════════════════════════════════════════════════════════╝

  Agent finishes responding
       │
       ▼
  WebSocket sends message: { type: "complete" }
       │
       ▼
  ChatPanel.tsx: onComplete callback fires
       │
       ▼
  configStore.ts: refreshAgents()
       │
       ├── GET /api/auth/agents?user_id=...
       ├── Response now contains agent_name = "chosen_name" (not "New Agent")
       └── set({ agents: res.agents })
       │
       ▼
  Sidebar.tsx: zustand reactivity re-renders agent list
       │
       └── Sidebar now shows "chosen_name" instead of "New Agent"


╔══════════════════════════════════════════════════════════════════════════════╗
║  STAGE 6: BOOTSTRAP OVER — ALL FUTURE MESSAGES                            ║
╚══════════════════════════════════════════════════════════════════════════════╝

  Next time anyone sends a message:
       │
       ▼
  build_complete_system_prompt() → Part 5 check:
       │
       ├── DB query: agent.created_by == user_id ?
       │      (yes or no, doesn't matter)
       │
       └── os.path.isfile("Bootstrap.md") → FALSE (file deleted)
              │
              ▼
           SKIP — no bootstrap injection
              │
              ▼
           Agent operates normally with:
           - Its chosen name in the DB
           - Its awareness profile (personality, creator info)
           - No trace of bootstrap in system prompt
```

---

## 2. Phase-by-Phase Logic

### Phase A: Agent Creation (backend/routes/auth.py)

**Trigger**: `POST /api/auth/agents` is called.

| Step | Action | Code Location |
|------|--------|---------------|
| 1 | Validate user exists | auth.py:156-163 |
| 2 | Generate `agent_id`, insert into DB | auth.py:166-182 |
| 3 | Build workspace path: `{base_working_path}/{agent_id}_{created_by}` | auth.py:190-193 |
| 4 | `os.makedirs(workspace_path, exist_ok=True)` | auth.py:194 |
| 5 | Write `BOOTSTRAP_MD_TEMPLATE` to `{workspace_path}/Bootstrap.md` | auth.py:196-198 |

**Failure handling**: Bootstrap write is wrapped in try/except. If it fails, the agent is still created — bootstrap is best-effort (auth.py:201-203).

**Result**: Every newly created agent gets a `Bootstrap.md` in its workspace.

---

### Phase B: System Prompt Injection (context_runtime.py)

**Trigger**: Every message to the agent runs `build_complete_system_prompt()`.

After building all normal prompt parts (Narrative, Events, Auxiliary Narratives, Module Instructions), the bootstrap check runs:

```
┌─────────────────────────────────────────────┐
│ Query AgentRepository for agent record      │
│                                             │
│   agent_record.created_by == ctx_data.user_id?  │
│          │                  │               │
│         YES                 NO              │
│          │                  │               │
│   Check Bootstrap.md     SKIP (no inject)   │
│   exists on disk?                           │
│     │           │                           │
│    YES          NO                          │
│     │           │                           │
│  Read file    SKIP (no inject)              │
│  & inject                                   │
│  into prompt                                │
└─────────────────────────────────────────────┘
```

| Condition | Result |
|-----------|--------|
| Current user IS the creator AND `Bootstrap.md` exists | Bootstrap instructions injected into system prompt (Part 5) |
| Current user IS the creator AND `Bootstrap.md` does NOT exist | No injection — bootstrap already completed |
| Current user is NOT the creator | No injection — regardless of whether file exists |
| Agent record not found (edge case) | No injection — guard clause fails safely |
| File read fails (permissions, etc.) | No injection — caught by except, logged as warning |

**Code location**: `context_runtime.py`, Part 5 block inside `build_complete_system_prompt()`.

**Key design decision**: We query `AgentRepository` directly instead of relying on `ctx_data.is_creator` from `BasicInfoModule`, because BasicInfoModule is not guaranteed to be loaded (it requires a DB instance record and may not be selected by the LLM instance decision).

---

### Phase C: The Bootstrap Conversation (agent-side, driven by template)

The injected `BOOTSTRAP_MD_TEMPLATE` instructs the agent to run a two-phase conversational Q&A:

```
Phase 1: Identity & Creator
─────────────────────────────
Agent asks: "What should I call myself? And who are you?"
  │
  ├── Creator gives BOTH name + their name → move to Phase 2
  ├── Creator gives only agent name → agent asks for creator name
  └── Creator gives only their name → agent asks for agent name

Phase 2: Personality & Purpose (optional)
─────────────────────────────────────────
Agent asks: "Want to tell me how I should behave, what I'm for?"
  │
  ├── Creator gives guidance → agent acknowledges
  └── Creator says skip/no/later → agent wraps up
```

**Rules from the template**:
- Keep it casual — feels like a 2-minute setup, not an interrogation
- Do NOT ask more than the two phases above
- If creator goes off-topic, finish bootstrap with whatever info is available

---

### Phase D: Tool Calls (agent-side)

Once the agent has at least the agent name, the template instructs it to:

| Order | Tool | Purpose | Server |
|-------|------|---------|--------|
| 1 | `update_agent_name(agent_id, new_name)` | Set the agent's display name in the DB | AwarenessModule MCP (port 7801) |
| 2 | `update_awareness(agent_id, new_awareness)` | Save name, creator, personality to awareness profile | AwarenessModule MCP (port 7801) |
| 3 | Delete `Bootstrap.md` | End bootstrap permanently | Claude SDK built-in file tools |

**`update_agent_name`** (awareness_module.py):
- Calls `AgentRepository.update_agent(agent_id, {"agent_name": new_name})`
- Returns success/error message
- This is what makes the sidebar name change

**`update_awareness`** (awareness_module.py, pre-existing tool):
- Writes a full Markdown awareness profile to `instance_awareness` table
- The agent follows the 4-section format from the tool's docstring
- Sections it fills during bootstrap: Role and Identity, Communication Style
- Everything else starts as "No specific observations yet" and evolves over time

---

### Phase E: Bootstrap.md Deletion (agent-side)

The agent deletes `Bootstrap.md` using Claude SDK's built-in file system tools. The file is in the agent's working directory (`agent_workspace/{agent_id}_{created_by}/`), which is the agent's CWD set at `step_3_agent_loop.py:149`.

**After deletion**:
- Next `build_complete_system_prompt()` call → `os.path.isfile()` returns `False` → no injection
- Bootstrap is permanently over for this agent
- The agent operates normally from this point on

---

### Phase F: Frontend Name Refresh

| Step | Action | Code Location |
|------|--------|---------------|
| 1 | Agent finishes responding → WebSocket sends `type: 'complete'` | useWebSocket.ts:73-76 |
| 2 | `onComplete` callback fires in ChatPanel | ChatPanel.tsx:37 |
| 3 | `refreshAgents()` called from configStore | configStore.ts:60-71 |
| 4 | Fresh agent list fetched from `GET /api/auth/agents` | configStore.ts:64 |
| 5 | Sidebar re-renders with updated agent name | Sidebar.tsx (reactive via zustand) |

---

## 3. Decision Matrix — All User Scenarios

| Scenario | Bootstrap.md exists? | User is creator? | Bootstrap injected? | Agent behavior |
|----------|---------------------|-------------------|---------------------|----------------|
| Creator's first message to new agent | YES | YES | YES | Runs bootstrap Q&A |
| Creator's second message (bootstrap not done) | YES | YES | YES | Continues bootstrap Q&A |
| Non-creator messages during bootstrap | YES | NO | NO | Normal agent behavior (no personality/name yet) |
| Creator after bootstrap completes | NO | YES | NO | Normal agent behavior |
| Non-creator after bootstrap completes | NO | NO | NO | Normal agent behavior |
| Existing agent (pre-feature, no Bootstrap.md) | NO | N/A | NO | Normal — zero impact |
| Creator deletes workspace manually | NO | YES | NO | Bootstrap stops, agent works normally |
| Agent forgets to delete Bootstrap.md | YES | YES | YES | Next conversation re-injects — self-healing |
| Creator sends off-topic during bootstrap | YES | YES | YES | Bootstrap persists as gentle reminder; agent finishes with whatever info it has |
| Multiple quick messages before bootstrap done | YES | YES | YES | Each turn sees bootstrap injection, agent continues the Q&A |

---

## 4. File Inventory

| File | Type | Role |
|------|------|------|
| `src/xyz_agent_context/bootstrap/__init__.py` | New | Package init |
| `src/xyz_agent_context/bootstrap/template.py` | New | `BOOTSTRAP_MD_TEMPLATE` — the content written to disk |
| `src/xyz_agent_context/context_runtime/prompts.py` | Modified | `BOOTSTRAP_INJECTION_PROMPT` — wrapper with priority header |
| `src/xyz_agent_context/context_runtime/context_runtime.py` | Modified | Part 5: reads Bootstrap.md, injects into system prompt |
| `backend/routes/auth.py` | Modified | Writes Bootstrap.md at agent creation |
| `src/xyz_agent_context/module/awareness_module/awareness_module.py` | Modified | `update_agent_name` MCP tool |
| `src/xyz_agent_context/prompts_index.py` | Modified | Registers new prompt constants |
| `frontend/src/stores/configStore.ts` | Modified | `refreshAgents()` shared action |
| `frontend/src/components/chat/ChatPanel.tsx` | Modified | Calls `refreshAgents()` on `onComplete` |
| `frontend/src/components/layout/Sidebar.tsx` | Modified | Uses shared `refreshAgents()` |

---

## 5. Key Design Decisions

1. **Direct DB check over BasicInfoModule**: The bootstrap injection queries `AgentRepository` directly rather than depending on `ctx_data.is_creator` from `BasicInfoModule`. This is because BasicInfoModule requires a DB instance record and may not be loaded by the LLM instance decision for a brand-new agent.

2. **File-based state over DB flag**: Bootstrap state is a file on disk (`Bootstrap.md`) rather than a DB column. This means:
   - No schema migration needed
   - The agent can delete it with built-in file tools
   - `os.path.isfile()` is a cheap check
   - Self-healing: if the agent fails to delete it, bootstrap re-injects next time

3. **Eager workspace creation**: Workspace is created at agent creation time (not lazily at first message). The existing lazy creation in `step_3_agent_loop.py` uses `exist_ok=True` so there's no conflict.

4. **Single MCP server**: `update_agent_name` lives on AwarenessModule's existing server (port 7801) rather than adding a new MCP server, avoiding port allocation, `DEFAULT_MCP_MODULES`, and `start/mcp.sh` changes.

5. **Non-fatal bootstrap write**: If Bootstrap.md fails to write at creation time, the agent still works — it just won't have the guided setup experience.
