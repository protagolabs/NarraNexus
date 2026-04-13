# NexusAgent Prompt Caching Analysis

## Goal
Understand which parts of the system prompt are static vs dynamic, and whether we can restructure for Anthropic prompt caching (like OpenClaw does).

---

## Current Prompt Structure

The final messages sent to Claude Code CLI look like this:

```
messages = [
  { "role": "system", "content": <enhanced_system_prompt> },   # 6-20 KB
  { "role": "user",   "content": <history_msg_1> },            # long-term memory
  { "role": "assistant", "content": <history_msg_2> },
  ...
  { "role": "user",   "content": <current_user_input> }        # this turn's input
]
```

Then in `xyz_claude_agent_sdk.py`, the system message is extracted and passed as `--system-prompt` CLI arg. Long-term history is flattened into a `=== Chat History ===` text block appended to the system prompt (because the SDK doesn't support multi-turn).

### Final effective prompt passed to Claude Code CLI:

```
[system_prompt arg] = Part 1 + Part 3 + Part 4 + Part 5 + Short-term Memory + Chat History
[user message]      = this turn's user input
```

**Everything is a single system prompt string + one user message.**

---

## System Prompt Breakdown (5 parts + 2 appendices)

### Part 1: Narrative Info (~1.5-2 KB)
**Source**: `context_runtime.py:344-348` → `PromptBuilder.build_main_prompt()`
**Template**: `NARRATIVE_MAIN_PROMPT_TEMPLATE` in `narrative/_narrative_impl/prompts.py:302`

Contains:
- Narrative system explanation (static boilerplate ~800 chars)
- Narrative ID, type, created_at, updated_at
- Name, description, current_summary
- Actor list

**Cacheability**: ✅ STABLE within a narrative session. Changes only on narrative switch.

### Part 2: Event History — DISABLED
Currently commented out. Zero impact.

### Part 3: Auxiliary Narratives (~0.5-2 KB)
**Source**: `context_runtime.py:376-398` → `_build_auxiliary_narratives_prompt()`

Contains:
- Summaries of related narratives (vector-similarity ranked)
- EverMemOS memory content for each

**Cacheability**: ❌ DYNAMIC every turn. Vector search returns different results per query.

### Part 4: Module Instructions (~3-6 KB)
**Source**: `context_runtime.py:401-404` → `_build_module_instructions_prompt()`

Composed of instructions from each loaded module:

| Module | Size | Dynamic? | Notes |
|--------|------|----------|-------|
| **BasicInfoModule** | ~2.5 KB | ⚠️ ONE LINE | `{current_time}` on line 104 of prompts.py changes every turn. Everything else (agent_id, name, description, creator_id, user_id) is stable per session. |
| **ChatModule** | ~0.8 KB | ✅ Static | Pure instructions, no placeholders |
| **MemoryModule** | ~0.5 KB | ✅ Static | Pure instructions |
| **JobModule** | ~1 KB | ⚠️ Has `{current_time_str}` | Time placeholder |
| **SkillModule** | ~0.5 KB | ✅ Static | Pure instructions |

**Cacheability**: ⚠️ MOSTLY STABLE — only `{current_time}` and `{current_time_str}` change per turn.

### Part 5: Bootstrap Injection (~0.2 KB)
**Source**: `context_runtime.py:406-428`

Static prompt telling Claude to read Bootstrap.md. Only for creator users.

**Cacheability**: ✅ STABLE.

### Appendix A: Short-term Memory (~1-3 KB)
**Source**: `context_runtime.py:566-571` → `_build_short_term_memory_prompt()`

Cross-narrative recent conversations. Appended to system prompt.

**Cacheability**: ❌ DYNAMIC — includes relative timestamps ("5 minutes ago"), different content per turn.

### Appendix B: Chat History (flattened, ~1-30 KB)
**Source**: `xyz_claude_agent_sdk.py:74-102`

Long-term conversation history, formatted as `User: ...\nAssistant: ...` and appended to system prompt.

**Cacheability**: ✅ INCREMENTALLY STABLE — only grows (new messages appended). Previous messages don't change.

---

## Size Summary

| Section | Typical Size | Stable? |
|---------|-------------|---------|
| Part 1: Narrative Info | 1.5-2 KB | ✅ Per narrative |
| Part 3: Auxiliary Narratives | 0.5-2 KB | ❌ Every turn |
| Part 4: Module Instructions | 3-6 KB | ⚠️ Except `current_time` |
| Part 5: Bootstrap | 0.2 KB | ✅ Always |
| Short-term Memory | 1-3 KB | ❌ Every turn |
| Chat History | 1-30 KB | ✅ Incremental |
| **Total** | **7-43 KB** | |

---

## The Problem: Why We Can't Cache Today

1. **Single monolithic string**: Everything is concatenated into one `system_prompt` string passed as a CLI arg. No way to mark cache breakpoints.

2. **Dynamic content mixed in early**: `{current_time}` in Part 4 (Module Instructions) and auxiliary narratives in Part 3 change every turn, invalidating the entire prefix.

3. **Claude Code CLI is a subprocess**: We don't control the Anthropic API call — the CLI builds its own `system` blocks internally.

4. **History in system prompt**: Chat history is appended to the system prompt text, not as separate messages. This means the prefix changes shape every turn.

---

## How OpenClaw Solves This

OpenClaw places two `cache_control: { type: "ephemeral" }` breakpoints:

```javascript
system: [
  { type: "text", text: "You are Claude Code...", cache_control: { type: "ephemeral" } },
  { type: "text", text: userSystemPrompt,         cache_control: { type: "ephemeral" } }
]
messages: [
  ...history,  // stable prefix, cached
  { role: "user", content: [{ type: "text", text: "...", cache_control: { type: "ephemeral" } }] }
]
```

This works because:
- System prompt is mostly static (tools + bootstrap files)
- History is separate messages that grow but don't change
- They call the Anthropic API directly (not via CLI subprocess)

---

## What We Could Do

### Option A: Restructure within Claude Agent SDK (Low effort)

The Claude Code CLI already uses prompt caching internally when it calls the Anthropic API. The key insight is that **Anthropic caches based on exact prefix matching** — if the first N tokens of the request are identical, those N tokens are cached.

**Optimization**: Reorder system prompt sections so STABLE content comes first:

```
CURRENT ORDER:                       OPTIMIZED ORDER:
1. Narrative Info (stable)           1. Narrative Info (stable)         ← same
2. Auxiliary Narratives (dynamic)    2. Module Instructions (stable*)
3. Module Instructions (mostly ok)   3. Bootstrap (stable)
4. Bootstrap (stable)                4. ─── cache likely breaks here ───
5. Short-term Memory (dynamic)       5. Auxiliary Narratives (dynamic)
+ Chat History (incremental)         6. Short-term Memory (dynamic)
                                     + Chat History (incremental)
```

*Move `{current_time}` out of Module Instructions into the dynamic section.

**Impact**: Small. Claude Code CLI's internal caching might get better prefix matches. But we still can't control `cache_control` breakpoints.

### Option B: Use `--continue` to reuse CLI subprocess (Medium effort)

The Claude Agent SDK supports `continue_conversation=True` which reuses the same Claude Code CLI session. This means:
- The CLI process stays alive across turns
- Claude Code's internal prompt caching works across turns
- System prompt only sent once

**Impact**: Potentially significant. The CLI's internal caching would work properly since the session persists.

**Risk**: Need to manage CLI process lifecycle, handle crashes/timeouts.

### Option C: Bypass CLI, call Anthropic API directly (High effort)

Replace `ClaudeSDKClient` with direct `anthropic.Anthropic` client calls. This gives full control over:
- `cache_control` breakpoints on system prompt blocks
- `cache_control` on the last user message
- Separate system blocks (static vs dynamic)
- Proper multi-turn message history

**Impact**: ~0.9s savings on API call (matching OpenClaw) + eliminate CLI startup overhead (~0.5-1s).

**Risk**: Lose Claude Code's built-in tools (file editing, bash, etc.) and MCP integration. Would need to reimplement or use a different tool framework.

### Option D: Hybrid — Direct API for simple chat, CLI for tool use (Medium-High effort)

Use direct Anthropic API calls for simple conversational turns (no tools needed), fall back to Claude Code CLI when tool use is required.

**Impact**: Best of both worlds for chat-heavy workloads.

---

## Recommendation

**Short term** (this sprint):
1. **Reorder system prompt** — put all stable content first (Option A). Free, might help.
2. **Move `{current_time}` to dynamic section** — stops it from invalidating the stable prefix.

**Medium term**:
3. **Explore `continue_conversation`** (Option B) — biggest bang for buck without architectural changes.

**Long term**:
4. **Direct API path for simple turns** (Option D) — maximum caching + no subprocess overhead.

---

## Key Files

| File | Role |
|------|------|
| `context_runtime.py:320-433` | `build_complete_system_prompt()` — assembles Parts 1-5 |
| `context_runtime.py:509-614` | `build_input_for_framework()` — adds short-term memory + builds messages list |
| `xyz_claude_agent_sdk.py:70-107` | Extracts system prompt, flattens history into it |
| `basic_info_module/prompts.py:104` | `{current_time}` — the cache-killer |
| `job_module/prompts.py:24` | `{current_time_str}` — another cache-killer |
| `narrative/_narrative_impl/prompts.py:302` | Narrative template (stable) |
