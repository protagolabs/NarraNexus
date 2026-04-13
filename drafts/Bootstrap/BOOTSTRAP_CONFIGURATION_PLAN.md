# Bootstrap Configuration Phase — Implementation Plan

**Date**: 2026-02-25
**Branch**: feat/gaia-benchmark (to be implemented on a new branch)
**Status**: Draft — approved for implementation

## Context

Currently, creating an agent produces a generic "New Agent" with no guided setup. The creator has no way to name the agent, define its personality, or introduce themselves through a structured first-run experience. This plan adds a bootstrap phase inspired by OpenClaw's bootstrapping but adapted to NexusMind's DB + module architecture.

**Outcome**: When a creator first messages a new agent, the agent conducts a natural conversational Q&A to collect its name, identity/tone, and owner info. The collected data flows into the existing awareness pipeline. Once complete, the agent deletes its bootstrap file and operates normally.

---

## Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Bootstrap signal | File existence (`Bootstrap.md`) | Clear, self-evident, no DB migration needed |
| Signal storage | Creator's workspace: `agent_workspace/{agent_id}_{created_by}/` | Agent can delete it with built-in file tools |
| Who triggers | Creator only (checked via `agents.created_by`) | `ctx_data.is_creator` already computed by BasicInfoModule |
| Data storage | Awareness pipeline (existing) + `agents.agent_name` | No new tables or schemas |
| Workspace creation | Eager (at agent creation, not first message) | Bootstrap.md must exist before context building |
| Agent name tool | Added to AwarenessModule's existing MCP server (port 7801) | Avoids creating new MCP process/port |
| Frontend refresh | Re-fetch agent list on WebSocket `onComplete` | `onComplete` callback already exists but unused |

---

## Changes Overview

| # | File | Change Type | Purpose |
|---|---|---|---|
| 1 | `src/xyz_agent_context/bootstrap/__init__.py` | **New** | Package init |
| 2 | `src/xyz_agent_context/bootstrap/template.py` | **New** | `BOOTSTRAP_MD_TEMPLATE` constant |
| 3 | `backend/routes/auth.py` | Modify | Create workspace + write Bootstrap.md at agent creation |
| 4 | `src/xyz_agent_context/context_runtime/prompts.py` | Modify | Add `BOOTSTRAP_INJECTION_PROMPT` constant |
| 5 | `src/xyz_agent_context/context_runtime/context_runtime.py` | Modify | Inject bootstrap section into system prompt when file exists |
| 6 | `src/xyz_agent_context/module/awareness_module/awareness_module.py` | Modify | Add `update_agent_name` MCP tool to existing server |
| 7 | `src/xyz_agent_context/prompts_index.py` | Modify | Register new prompt constants |
| 8 | `frontend/src/stores/configStore.ts` | Modify | Add shared `refreshAgents()` action |
| 9 | `frontend/src/components/chat/ChatPanel.tsx` | Modify | Call `refreshAgents()` on WebSocket `onComplete` |
| 10 | `frontend/src/components/layout/Sidebar.tsx` | Modify | Use shared `refreshAgents()` from store |

**Total**: 2 new files, 8 modified files. No DB schema changes. No new MCP servers/ports.

---

## Step 1: Bootstrap Template

### New: `src/xyz_agent_context/bootstrap/__init__.py`
Empty init file.

### New: `src/xyz_agent_context/bootstrap/template.py`
Define `BOOTSTRAP_MD_TEMPLATE` — the content written into the creator's workspace.

Content should be conversational instructions (NOT a rigid form), two phases:
- **Phase 1**: "Hey — I just came online. What should I call myself? And who are you?" (collect agent name + owner name)
- **Phase 2**: "I know who I am now. Want to tell me anything about how I should behave, what I'm for, or how I should talk? No pressure — you can always shape me later just by telling me."
- **When done**: Call `update_agent_name` tool, let awareness update naturally, delete `Bootstrap.md`

### Modify: `src/xyz_agent_context/prompts_index.py`
Add section 11 importing `BOOTSTRAP_MD_TEMPLATE` from the new package.

---

## Step 2: Eager Workspace Creation + Bootstrap.md Write

### Modify: `backend/routes/auth.py` — `create_agent()` (lines 144-202)

**After** the DB insert succeeds (after line 182), **before** building the response (line 185):

1. Import `os` and `settings` at file top
2. Import `BOOTSTRAP_MD_TEMPLATE` from `xyz_agent_context.bootstrap.template`
3. Build workspace path: `os.path.join(settings.base_working_path, f"{agent_id}_{request.created_by}")`
4. `os.makedirs(workspace_path, exist_ok=True)`
5. Write `BOOTSTRAP_MD_TEMPLATE` to `{workspace_path}/Bootstrap.md`

This moves workspace creation from lazy (`step_3_agent_loop.py:148-151`) to eager. The existing lazy creation in step_3 uses `exist_ok=True` so it won't conflict.

---

## Step 3: Context Injection — Bootstrap Prompt in System Prompt

### Modify: `src/xyz_agent_context/context_runtime/prompts.py`
Add `BOOTSTRAP_INJECTION_PROMPT` constant — a wrapper that:
- Tells the agent it's in bootstrap mode
- Says "this takes priority"
- Contains a `{bootstrap_content}` placeholder for the Bootstrap.md content

### Modify: `src/xyz_agent_context/context_runtime/context_runtime.py` — `build_complete_system_prompt()` (line 310-408)

**After** the Module Instructions block (after line 403), **before** the final join (line 406), add a new Part 5:

```python
# ========================================================================
# Part 5: Bootstrap Configuration (if active)
# Only injected when the current user is the creator AND Bootstrap.md exists
# ========================================================================
if ctx_data.is_creator and ctx_data.creator_id:
    bootstrap_path = os.path.join(
        settings.base_working_path,
        f"{self.agent_id}_{ctx_data.creator_id}",
        "Bootstrap.md"
    )
    if os.path.isfile(bootstrap_path):
        try:
            with open(bootstrap_path, "r", encoding="utf-8") as f:
                bootstrap_content = f.read()
            bootstrap_section = BOOTSTRAP_INJECTION_PROMPT.format(
                bootstrap_content=bootstrap_content
            )
            prompt_parts.append(bootstrap_section)
            logger.info("        Added Bootstrap injection to system prompt")
        except Exception as e:
            logger.warning(f"        Failed to read Bootstrap.md: {e}")
```

**Why this works**:
- `BasicInfoModule.hook_data_gathering()` runs at line 139, which populates `ctx_data.is_creator` and `ctx_data.creator_id` **BEFORE** `build_complete_system_prompt()` at line 162
- Non-creator users: `is_creator=False` → bootstrap block skipped entirely
- After file deleted: `os.path.isfile()` returns `False` → block skipped
- Existing agents (no Bootstrap.md): block skipped — zero impact

---

## Step 4: Agent Name Update Tool

### Modify: `src/xyz_agent_context/module/awareness_module/awareness_module.py` — `create_mcp_server()`

Add a second `@mcp.tool()` inside the existing `create_mcp_server()` method (which already runs on port 7801):

```python
@mcp.tool()
async def update_agent_name(agent_id: str, new_name: str) -> str:
    """
    Update the agent's display name.
    Call this when your creator tells you what your name should be.
    """
    db = await AwarenessModule.get_mcp_db_client()
    from xyz_agent_context.repository import AgentRepository
    repo = AgentRepository(db)
    affected = await repo.update_agent(agent_id, {"agent_name": new_name})
    if affected > 0:
        return f"Name updated to '{new_name}' successfully."
    else:
        return f"Error: Agent {agent_id} not found or no change made."
```

**Why AwarenessModule, not BasicInfoModule**: BasicInfoModule has NO MCP server (returns `server_url=""`, `type="None"`). Adding a full MCP server there would require a new port (7806), adding to `DEFAULT_MCP_MODULES` in `module_runner.py`, updating `MODULE_PORTS`, and updating `start/mcp.sh`. Adding one tool to AwarenessModule's existing server is a single function — zero infrastructure change.

---

## Step 5: Frontend — Dynamic Name Refresh

### Modify: `frontend/src/stores/configStore.ts`
Add a `refreshAgents()` action that calls `api.getAgents(userId)` and updates `agents` state. This centralizes agent-list fetching.

### Modify: `frontend/src/components/chat/ChatPanel.tsx` (line 36-42)
The `useAgentWebSocket` hook already supports `onComplete` callback (see `useWebSocket.ts:20-21`, called at line 75 when `message.type === 'complete'`). Currently ChatPanel only uses `onMessage` and `onClose`. Add:

```typescript
const { run, isLoading } = useAgentWebSocket({
    onMessage: processMessage,
    onComplete: () => {
        refreshAgents();  // Pick up any name changes from bootstrap
    },
    onClose: () => {
        stopStreaming();
    },
});
```

### Modify: `frontend/src/components/layout/Sidebar.tsx`
Refactor `fetchAgents()` to use the shared `refreshAgents()` from configStore.

---

## Step 6: Bootstrap.md Deletion (No Code Changes)

The agent deletes `Bootstrap.md` itself using Claude SDK's built-in file tools. The file is in `agent_workspace/{agent_id}_{created_by}/` which IS the agent's working directory (set at `step_3_agent_loop.py:149`). The Bootstrap.md template instructions tell the agent to delete it when done.

---

## Edge Cases

| Case | Behavior |
|---|---|
| Non-creator messages during bootstrap | `is_creator=False` → no bootstrap injection, agent works normally |
| Creator sends off-topic during bootstrap | Bootstrap injection persists as gentle reminder until file deleted |
| Existing agents (pre-feature) | No Bootstrap.md → no injection → zero impact |
| Creator deletes workspace manually | `os.path.isfile()` → False → bootstrap stops |
| Agent forgets to delete file | Next conversation, bootstrap re-injects → self-healing |
| Second user before bootstrap done | Works normally, just no personality/name yet |
| Creator skips Phase 2 (identity) | Fine — Phase 2 is optional, awareness updates naturally over time |
| Bootstrap interrupted mid-conversation | Bootstrap.md still exists, resumes next time creator messages |

---

## Future Considerations (Not in scope now)

- **UI awareness update skips bootstrap**: If someone writes to `instance_awareness` via the UI, also delete `Bootstrap.md`
- **Per-agent-type templates**: Different `BOOTSTRAP_MD_TEMPLATE` variants based on `agent_type`
- **Bootstrap timeout**: Auto-delete Bootstrap.md after N days if never completed
- **Skip bootstrap button**: Frontend UI option to skip bootstrap entirely

---

## Verification Checklist

1. **Create a new agent** → verify `agent_workspace/{agent_id}_{creator}/Bootstrap.md` exists
2. **Send first message as creator** → verify system prompt contains bootstrap section
3. **Answer bootstrap questions** → verify agent asks name, then identity
4. **After agent calls `update_agent_name`** → verify `agents.agent_name` updated in DB, sidebar refreshes
5. **After agent deletes Bootstrap.md** → verify next message has NO bootstrap section in prompt
6. **Send message as non-creator** → verify NO bootstrap injection regardless of file state
7. **Existing agents** → verify no Bootstrap.md, no injection, no behavior change

---

## Key File References

| Component | File | Key Lines |
|---|---|---|
| Agent creation endpoint | `backend/routes/auth.py` | 144-202 |
| Agent model (DB schema) | `src/xyz_agent_context/schema/entity_schema.py` | 146-159 |
| Context building orchestrator | `src/xyz_agent_context/context_runtime/context_runtime.py` | 79-175 (run), 310-408 (build_complete_system_prompt) |
| ContextData schema | `src/xyz_agent_context/schema/context_schema.py` | 18-59 |
| BasicInfoModule (populates is_creator) | `src/xyz_agent_context/module/basic_info_module/basic_info_module.py` | 64-114 |
| AwarenessModule MCP server | `src/xyz_agent_context/module/awareness_module/awareness_module.py` | 148-230 |
| Prompt index | `src/xyz_agent_context/prompts_index.py` | 1-116 |
| Lazy workspace creation (existing) | `src/xyz_agent_context/agent_runtime/_agent_runtime_steps/step_3_agent_loop.py` | 147-151 |
| Settings (base_working_path) | `src/xyz_agent_context/settings.py` | 55 |
| WebSocket hook (onComplete exists) | `frontend/src/hooks/useWebSocket.ts` | 20-21, 73-77 |
| ChatPanel (wire onComplete) | `frontend/src/components/chat/ChatPanel.tsx` | 36-42 |
| Sidebar (agent name display) | `frontend/src/components/layout/Sidebar.tsx` | 43-64, 424 |
| Config store (agents state) | `frontend/src/stores/configStore.ts` | 26, 32, 56 |
| MCP module runner | `src/xyz_agent_context/module/module_runner.py` | 99-114 |
