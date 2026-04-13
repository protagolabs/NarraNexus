# OpenClaw vs NexusAgent: TTFT Comparison

## Section 1: Agent Loop Comparison

Side-by-side comparison of both agent pipelines from WebSocket message to first token.

### NexusAgent (7 steps, TTFT-critical = Steps 0-3)

```
User WS message
├── Step 0: Init (agent config, session, event)           ~15ms
├── Step 1: Narrative Selection                            ~4.8s
│   ├── Continuity LLM (gpt-4o-mini)                      ~2.0s
│   ├── EverMemOS search                                   ~1.0s
│   └── Judge LLM (gpt-4o-mini)                           ~1.8s
├── Step 1.5: Init Markdown                                ~1ms
├── Step 2: Module Loading (skipped w/ flag)                ~5ms
├── Step 2.5: Sync Instances                               ~26ms
├── Step 3: Context Build + Claude SDK                     ~3.0s
│   ├── Context assembly                                   ~30ms
│   └── Claude API → first token                           ~3.0s
═══ FIRST TOKEN ═══  (total: ~7.9s)
├── Step 4: Persist                                        ~266ms
└── Step 5: Hooks (memory, social, chat persist)           ~10s
```

### OpenClaw (single-pass, no auxiliary LLMs)

```
User WS message
├── Workspace + model resolve                              ~3ms
├── Auth profile resolve                                   ~4ms
├── runEmbeddedAttempt                                     (starts at ~5ms)
│   ├── Sandbox resolve                                    ~1ms
│   ├── Skills resolve                                     ~1ms
│   ├── Bootstrap context (workspace files)                ~5ms
│   ├── System prompt build                                ~22ms
│   ├── Session file open + load                           ~23ms
│   ├── createAgentSession (tools, settings)               ~28ms
│   ├── Stream function setup                              ~28ms
│   └── Anthropic API call (claude-sonnet-4-6)             ~2100ms
│       └── First content_block_delta arrives
═══ FIRST TOKEN ═══  (total: ~2.13s measured)
└── Save response to session JSONL
```

**Key insight**: Total pre-API overhead is only **~33ms**. The Anthropic API call
(~2100ms) accounts for **98.5%** of the total TTFT.

---

## Section 2: Detailed Timing (from test logs)

> **Status**: Measured on 2026-03-09 using instrumented source build.
> Test: "Say hello in one sentence." via WebSocket to local gateway.
> Model: anthropic/claude-sonnet-4-6, thinking=adaptive

### Measured timing (12 stages)

From `run.ts` (outer loop, relative to run start):
| Stage | Log tag | elapsedMs |
|-------|---------|-----------|
| Workspace resolved | `[ttft] workspace resolved` | 1 |
| Model resolved | `[ttft] model resolved` | 3 |
| Auth resolved | `[ttft] auth resolved` | 4 |
| Starting attempt | `[ttft] starting attempt` | 5 |

From `attempt.ts` (inner loop, relative to attempt start):
| Stage | Log tag | elapsedMs |
|-------|---------|-----------|
| Embedded run start | `[ttft] embedded run start` | 0 |
| Sandbox resolved | `[ttft] sandbox resolved` | 1 |
| Skills resolved | `[ttft] skills resolved` | 1 |
| Bootstrap context loaded | `[ttft] bootstrap context loaded` | 5 |
| System prompt built | `[ttft] system prompt built` | 22 |
| Session file opened | `[ttft] session file opened` | 23 |
| Agent session created | `[ttft] agent session created` | 28 |
| Stream function setup | `[ttft] stream function setup` | 28 |

**End-to-end (from WS test client):**
| Measurement | Value |
|-------------|-------|
| WS connect → chat.send | ~8ms |
| chat.send → first delta | ~2127ms |
| Total TTFT (connect → first delta) | ~2135ms |
| Pre-API overhead (outer + inner) | ~33ms |
| Anthropic API call (estimated) | ~2100ms |

**Note**: The `run.ts` timestamps are relative to the overall `started` time. The `attempt.ts` timestamps are relative to when `runEmbeddedAttempt` begins. Total absolute time = outer elapsed + inner elapsed = 5 + 28 = 33ms of non-API work.

---

## Section 3: Key Architectural Differences

| Aspect | NexusAgent | OpenClaw |
|--------|-----------|----------|
| **Auxiliary LLM calls before main API** | 2 (continuity + judge, gpt-4o-mini) | 0 |
| **Memory retrieval** | EverMemOS search (~1s) during narrative selection | Session JSONL file (local disk I/O) |
| **Narrative/context selection** | LLM-based narrative continuity + retrieval judge | Static system prompt + bootstrap files from workspace |
| **System prompt assembly** | Dynamic context build from narrative + modules | Static bootstrap files + skills prompt |
| **Session state** | In-memory + persistence hooks | JSONL file (append-only) |
| **Prompt caching** | Not used | Anthropic prompt caching (cache_control breakpoints) |
| **Main LLM provider** | Claude (Anthropic) | Claude (Anthropic) — same provider |
| **Module system** | Dynamic module loading (skippable) | Skills system (loaded from workspace) |
| **Post-response hooks** | Memory persistence, social hooks, chat persist (~10s) | Append to session JSONL |

### Why OpenClaw is faster

1. **Zero auxiliary LLM calls**: The biggest factor. NexusAgent spends ~4.8s on two gpt-4o-mini calls (continuity + judge) before even starting the main Claude API call. OpenClaw has no pre-processing LLM calls.

2. **Local-only session state**: OpenClaw reads session history from a local JSONL file (append-only). NexusAgent queries EverMemOS (network call).

3. **Anthropic prompt caching with two breakpoints**: OpenClaw places `cache_control: { type: "ephemeral" }` at two strategic points, giving the Anthropic API server-side cache hits on repeated calls. (See Section 5 below.)

4. **Simpler context pipeline**: OpenClaw assembles the system prompt from static files (bootstrap + skills). NexusAgent runs a dynamic narrative selection pipeline.

---

## Section 4: OpenClaw's SDK Architecture (`@mariozechner/pi-*`)

OpenClaw does NOT use the Claude Agent SDK. It uses a custom three-layer framework:

### Stack

```
@mariozechner/pi-agent-core   (v0.57.1) — Agent class, event loop, message types
@mariozechner/pi-coding-agent  (v0.57.1) — AgentSession, tools, SessionManager, compaction
@mariozechner/pi-ai            (v0.57.1) — Streaming wrapper around @anthropic-ai/sdk
```

### Call path (message → API)

```
OpenClaw gateway receives WS message
  → runEmbeddedAttempt (builds context, tools, system prompt)
    → createAgentSession (registers tools, restores session from JSONL)
      → session.prompt(userMessage)
        → Agent._runLoop()
          → streamSimple() [pi-ai]
            → streamSimpleAnthropic() [wrapper: resolves thinking config]
              → streamAnthropic() [creates Anthropic SDK client, builds params]
                → client.messages.stream({ ...params, stream: true })
                  → Events emitted directly to listeners (no intermediate buffer)
```

### Key design decisions for speed

1. **Lazy initialization**: `createAgentSession` defers expensive work. System prompt built once, only rebuilt when tools change (not per-turn).

2. **Cached system prompt**: `_baseSystemPrompt` is cached at session init. Per-turn modifications happen via extension hooks, not full rebuilds.

3. **Append-only session storage**: SessionManager uses JSONL with tree structure (id/parentId). No file rewrites — just appends. Compaction happens async after `agent_end`, never blocks the message loop.

4. **Non-blocking event pipeline**: Agent events stream directly from the Anthropic SDK to listeners. Event handlers are queued but not awaited — the stream isn't blocked by persistence or hooks.

5. **Conservative token estimation**: Uses `chars/4` heuristic for compaction decisions instead of calling a tokenizer.

6. **One-at-a-time steering**: Default mode processes one steering/follow-up message per turn, keeping context small.

---

## Section 5: Prompt Caching Strategy (the ~0.9s advantage)

OpenClaw's `@mariozechner/pi-ai` places **two `cache_control` breakpoints** in every Anthropic API call:

### Breakpoint 1: System prompt blocks

```javascript
// anthropic.js — buildParams()
system: [
  { type: "text", text: "You are Claude Code...", cache_control: { type: "ephemeral" } },
  { type: "text", text: userSystemPrompt,         cache_control: { type: "ephemeral" } }
]
```

This caches the **entire system prompt** server-side. Since OpenClaw's system prompt is mostly static (tools + bootstrap files), this gets a cache hit on every subsequent message in the same session.

### Breakpoint 2: Last user message (final block)

```javascript
// anthropic.js — buildParams()
// Applies cache_control to the LAST BLOCK of the FINAL user message
messages: [
  ...history,
  { role: "user", content: [
    { type: "text", text: "user message", cache_control: { type: "ephemeral" } }
  ]}
]
```

This creates **two cache regions**:
1. System context (static, reused across turns)
2. Conversation history up to the last user message (grows each turn, cached for tool-use loops)

### Cache retention

- Default: "short" (ephemeral, ~5 min TTL on Anthropic servers)
- Configurable to "long" (1h TTL, only on api.anthropic.com)
- Configurable to "none" (disabled)

### Impact on TTFT

With prompt caching, the Anthropic API can skip re-processing cached input tokens. For a ~28K char system prompt, this saves significant processing time on repeated calls. Our measurement shows OpenClaw's API call takes ~2.1s — NexusAgent's takes ~3.0s. The ~0.9s gap is likely attributable to prompt caching.

---

## Section 6: Optimization Opportunities for NexusAgent

### High Impact (targets the ~4.8s narrative selection)

1. **Eliminate or defer auxiliary LLM calls**
   - The continuity + judge LLM calls account for ~4.8s of the 7.9s TTFT
   - Option A: Cache narrative selections and reuse for identical/similar contexts
   - Option B: Move narrative selection to a post-first-token background task
   - Option C: Replace LLM-based selection with rule-based heuristics for common cases

2. **Parallelize remaining LLM calls**
   - If auxiliary calls can't be eliminated, run continuity + EverMemOS + judge in parallel where possible
   - Currently sequential: continuity (2.0s) → EverMemOS (1.0s) → judge (1.8s)
   - Could overlap EverMemOS with continuity

### Medium Impact (targets the ~3.0s API call)

3. **Enable Anthropic prompt caching**
   - Add `cache_control: { type: "ephemeral" }` to system prompt blocks in Claude SDK calls
   - Add cache breakpoint to the last user message
   - NexusAgent's system prompt has static portions (agent persona, tool definitions) that would cache well
   - Potential savings: ~0.9s on the main API call (matching OpenClaw's 2.1s)

4. **Adopt non-blocking event streaming**
   - OpenClaw's event pipeline doesn't await persistence before processing next event
   - NexusAgent's Step 4 (Persist, 266ms) and Step 5 (Hooks, 10s) should be fully async/background

### Lower Impact (incremental gains)

5. **Session state optimization**
   - Consider local caching of EverMemOS results (OpenClaw uses local JSONL, 134ms memory_search)
   - Pre-warm session state on WebSocket connect (before first message)

6. **System prompt caching at application level**
   - Like OpenClaw's `_baseSystemPrompt` pattern: build once, only rebuild when tools change
   - Avoid rebuilding the full context on every message

7. **Pre-warm connections**
   - Keep persistent HTTP/2 connections to Anthropic API
   - DNS pre-resolution for external services

### Theoretical minimum TTFT for NexusAgent

Based on measured OpenClaw data (33ms pre-API overhead, ~2.1s API call):

If auxiliary LLM calls are eliminated + prompt caching enabled:
- Init: ~15ms
- Context build: ~30ms
- Claude API first token: ~2.1s (with prompt caching, matching OpenClaw)
- **Theoretical minimum: ~2.1s** (vs current ~7.9s, a 3.8x improvement)

The gap breaks down as:
- **~4.8s** from auxiliary LLM calls (continuity + judge) — **eliminable**
- **~0.9s** from API call overhead vs OpenClaw — **reducible via prompt caching**
- **~0.1s** from other overhead — **negligible**
