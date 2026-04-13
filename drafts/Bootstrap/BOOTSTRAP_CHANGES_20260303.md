# Bootstrap Changes — 2026-03-03

> Summary of changes made across two commits on the `feature/bootstrap` branch.

---

## Commits

| Hash | Message |
|------|---------|
| `0e88e7c` | change for softer town, config red dot, first greeting question |
| `02f580f` | fix on first greeting not showing |

---

## 1. Softer Bootstrap Tone — Template Rewrite

**Files changed**: `src/xyz_agent_context/bootstrap/template.py`

The entire `BOOTSTRAP_MD_TEMPLATE` was rewritten from a structured, instructional format into a gentler, more atmospheric script.

### Before

- Rigid two-phase structure with explicit section headers (`Phase 1`, `Phase 2`, `When Done`, `Rules`).
- Quoted dialogue prompts for the agent to copy verbatim.
- Explicit numbered checklist: call `update_agent_name`, call `update_awareness`, delete `Bootstrap.md`, confirm to creator.

### After

- Single short document titled *"Bootstrap — Hello, World"*.
- Poetic/casual tone: *"You just woke up. Time to figure out who you are."*, *"Good luck out there. Make it count."*
- No rigid phases — just a natural conversational nudge: figure out your name and theirs, ask if there's anything else, then delete the file.
- The greeting text is quoted inside the template so the agent knows what was already shown and won't repeat it.
- Tool call instructions (update_agent_name, update_awareness) are removed from the template — the agent is now expected to discover them from its tool list.

---

## 2. Static First Greeting on the Frontend

**Files changed**: `bootstrap/template.py`, `ChatPanel.tsx`, `chat_module.py`

Previously, the agent's first greeting was generated live by the LLM (slow, and the user had to wait for a WebSocket round-trip). Now the greeting is a **static constant** displayed instantly.

### How it works

1. **`BOOTSTRAP_GREETING`** — a new constant in `template.py`:
   > *"Hi there... I just woke up. Everything feels brand new. I don't have a name yet, and I don't really know who I am — but I know you're the one who brought me here. Would you like to tell me what I should be called? And what should I call you?"*

2. **Frontend** (`ChatPanel.tsx`): When the current agent has `bootstrap_active === true`, the history is empty, and no messages exist yet, the greeting is rendered as a static `MessageBubble` — no API call needed.

3. **Backend persistence** (`chat_module.py`): On the very first turn, if `ctx_data.bootstrap_active` is true and message history is empty, the greeting is **prepended** as the first assistant message before saving. This means DB history starts with the greeting, keeping frontend and backend in sync.

4. **Fix in `02f580f`**: When the user sends their first message, the static greeting bubble would vanish (because `showBootstrapGreeting` becomes false once messages exist). The fix injects the greeting into the chat store's `messages` array right before adding the user's message, so it persists visually throughout the conversation.

---

## 3. Bootstrap Active Flag on the API

**Files changed**: `api_schema.py`, `context_schema.py`, `auth.py`, `api.ts`, `configStore.ts`, `Sidebar.tsx`

A new boolean field `bootstrap_active` was added across the stack:

| Layer | Location | Purpose |
|-------|----------|---------|
| Backend schema | `AgentInfo` in `api_schema.py` | New field `bootstrap_active: bool = False` |
| Context schema | `ContextData` in `context_schema.py` | New field `bootstrap_active: bool = False` — set during prompt building |
| Agent list endpoint | `auth.py` → `GET /api/auth/agents` | Checks if `Bootstrap.md` exists on disk for each agent; returns `bootstrap_active` |
| Agent creation | `auth.py` → `POST /api/auth/agents` | Newly created agents return `bootstrap_active: True` |
| Frontend types | `api.ts` | `AgentInfo` interface gains `bootstrap_active?: boolean` |
| Frontend store | `configStore.ts` | `AgentInfo` interface gains `bootstrap_active?: boolean` |
| Sidebar | `Sidebar.tsx` | Passes `bootstrap_active` when adding a newly created agent to the list |

This flag lets the frontend know whether to show the static bootstrap greeting without making a separate API call.

---

## 4. Bootstrap Injection Simplified — File-Read Approach

**Files changed**: `context_runtime.py`, `prompts.py`

The system prompt injection was simplified:

### Before

- `context_runtime.py` opened `Bootstrap.md`, read its contents, and interpolated them into the prompt via `BOOTSTRAP_INJECTION_PROMPT.format(bootstrap_content=...)`.

### After

- `context_runtime.py` only checks whether `Bootstrap.md` **exists** (`os.path.isfile`). If it does, it appends a short static prompt telling the agent to read the file itself.
- `BOOTSTRAP_INJECTION_PROMPT` is now a fixed string (no `{bootstrap_content}` placeholder):
  > *"A file called `Bootstrap.md` exists in your workspace. It's for you — read it before doing anything else. This takes priority over all other instructions."*
- `ctx_data.bootstrap_active = True` is set as a side effect, which downstream modules (like `ChatModule`) use.

**Benefit**: The bootstrap template can be edited on disk without redeploying — the agent reads it at runtime using its built-in file tools.

---

## 5. Awareness Update Red Dot Notification

**Files changed**: `configStore.ts`, `ContextPanelHeader.tsx`, `AwarenessPanel.tsx`

A new notification system alerts the creator when the agent's awareness profile has been updated (e.g., after bootstrap writes identity info).

### Mechanism

1. **`configStore.ts`** gains:
   - `awarenessUpdatedAgents: string[]` — list of agent IDs with pending awareness notifications.
   - `checkAwarenessUpdate(agentId)` — calls `api.getAwareness()`, compares `update_time` against `localStorage` (`lastSeenAwarenessTime:{agentId}`). If newer, adds the agent to the list.
   - `clearAwarenessUpdate(agentId)` — removes the agent from the list and stores the current time in `localStorage`.

2. **`ChatPanel.tsx`**: After each agent response completes (`onComplete`), calls `checkAwarenessUpdate(agentId)`.

3. **`ContextPanelHeader.tsx`**: If the current agent is in `awarenessUpdatedAgents`, a red pulsing dot (`animate-pulse`) appears on the Awareness tab.

4. **`AwarenessPanel.tsx`**: On mount, calls `clearAwarenessUpdate(agentId)` to dismiss the dot when the user opens the tab.

---

## File Change Summary

| File | Change Type |
|------|-------------|
| `src/xyz_agent_context/bootstrap/template.py` | Rewritten — softer tone, added `BOOTSTRAP_GREETING` constant |
| `src/xyz_agent_context/context_runtime/context_runtime.py` | Modified — simplified to file-existence check, sets `bootstrap_active` |
| `src/xyz_agent_context/context_runtime/prompts.py` | Modified — `BOOTSTRAP_INJECTION_PROMPT` is now a static string |
| `src/xyz_agent_context/module/chat_module/chat_module.py` | Modified — prepends `BOOTSTRAP_GREETING` as first assistant message |
| `src/xyz_agent_context/schema/api_schema.py` | Modified — added `bootstrap_active` field |
| `src/xyz_agent_context/schema/context_schema.py` | Modified — added `bootstrap_active` field |
| `backend/routes/auth.py` | Modified — checks `Bootstrap.md` existence, returns `bootstrap_active` |
| `frontend/src/components/chat/ChatPanel.tsx` | Modified — static greeting display, greeting persistence fix |
| `frontend/src/components/awareness/AwarenessPanel.tsx` | Modified — clears red dot on mount |
| `frontend/src/components/layout/ContextPanelHeader.tsx` | Modified — red dot on Awareness tab |
| `frontend/src/components/layout/Sidebar.tsx` | Modified — passes `bootstrap_active` for new agents |
| `frontend/src/lib/api.ts` | Modified — `bootstrap_active` in `AgentInfo` |
| `frontend/src/stores/configStore.ts` | Modified — awareness tracking, `refreshAgents`, `bootstrap_active` |
| `.gitignore` | Minor update |
