# NexusAgent Speed Analysis & Optimization Plan

**Part 2: Agent Loop Timing Analysis and TTFT Optimization**

---

## 1. Objective

Reduce **Time-to-First-Token (TTFT)** — the wall-clock time from when a user sends a message to when the first visible streaming token (thinking or text) appears on the frontend via WebSocket.

---

## 2. Agent Loop Overview

Every user message flows through a **7-step pipeline** in `AgentRuntime.run()`. Steps 0–3.3 are **blocking prerequisites** before the main LLM can start generating tokens. Steps 4–6 run **after** the response is streamed, so they don't affect TTFT but do affect total turn time.

```
User sends message
    │
    ▼
┌─ TTFT-CRITICAL PATH (blocking) ──────────────────────────────┐
│                                                                │
│  Step 0:   Initialize (DB reads, event, session)               │
│  Step 1:   Select Narrative                                    │
│            ├─ Continuity Detection (LLM call)                  │
│            ├─ EverMemOS / Vector Search                        │
│            └─ LLM Judge (if low confidence)                    │
│  Step 1.5: Init Markdown (file read)                           │
│  Step 2:   Load Modules                                        │
│            ├─ LLM Instance Decision (LLM call)                 │
│            └─ Module Object Creation                           │
│  Step 2.5: Sync Instances (DB writes)                          │
│  Step 3:   Context Build + Claude SDK startup + MCP handshake  │
│                                                                │
│  ══════ FIRST TOKEN ARRIVES HERE ══════                        │
│                                                                │
└────────────────────────────────────────────────────────────────┘
    │
    ▼  (user already seeing streamed response)
┌─ POST-RESPONSE PATH ─────────────────────────────────────────┐
│  Step 3:   Agent Loop continues (tool calls, reasoning)       │
│  Step 4:   Persist Results (trajectory, event, narrative)     │
│  Step 5:   Execute Hooks (memory, social, chat persist)       │
│  Step 6:   Process Callbacks (dependency triggers)            │
└───────────────────────────────────────────────────────────────┘
```

---

## 3. Timing Analysis from Production Logs

### 3.1 Log Sample A — Same-Topic Follow-Up (Mar 3, agent_77909c41f618)

**Input**: `"not much, just see you are my assistant and you talk politely right now."`
**Scenario**: Continuity passes (same topic), no vector search needed, simple chat response.

| Step | Start | End | Duration | What Happened |
|------|-------|-----|----------|---------------|
| **Step 0** (Init) | 13:47:47.513 | 13:47:47.528 | **~15ms** | Agent config, event create, session load |
| **Step 1** (Narrative) | 13:47:47.529 | 13:47:51.976 | **~4.4s** | Continuity LLM: 4.3s |
| **Step 1.5** (Markdown) | 13:47:51.993 | 13:47:51.994 | **~1ms** | File read |
| **Step 2** (Modules) | 13:47:51.994 | 13:47:54.935 | **~2.9s** | Instance decision LLM: 2.9s |
| **Step 2.5** (Sync) | 13:47:54.938 | 13:47:54.964 | **~26ms** | DB writes |
| **Step 3** (Context build) | 13:47:54.965 | 13:47:54.989 | **~24ms** | Data gathering, prompt assembly |
| **Step 3.4** (SDK → 1st token) | 13:47:54.990 | 13:48:03.743 | **~8.7s** | Claude SDK startup + MCP connect + first thinking token |

```
TOTAL TTFT:  ~16.2 seconds

User message received:    13:47:47.513
First thinking WS sent:   13:48:03.744
```

#### Step 1 Sub-Breakdown (Same-Topic Case)

| Sub-phase | Duration | Details |
|-----------|----------|---------|
| Continuity LLM call (`gpt-5.1`) | **~4.3s** | Simple yes/no → returned "same topic" |
| Vector search | skipped | Continuity passed, no need |
| LLM judge | skipped | Continuity passed, no need |

### 3.2 Log Sample B — New Topic with EverMemOS (Feb 27, agent_2be97fdd0b81)

**Input**: `"How many pages if the 2023 IPCC report (85 pages version) mentions nuclear energy?"`
**Scenario**: Continuity fails (different topic), EverMemOS search + low-confidence LLM judge, heavy tool-calling response.

| Step | Start | End | Duration | What Happened |
|------|-------|-----|----------|---------------|
| **Step 0** (Init) | 13:39:13.318 | 13:39:13.325 | **~7ms** | Agent config, event create, session load |
| **Step 1** (Narrative) | 13:39:13.325 | 13:39:18.483 | **~5.2s** | See sub-breakdown below |
| **Step 1.5** (Markdown) | 13:39:18.483 | 13:39:18.484 | **~1ms** | File read |
| **Step 2** (Modules) | 13:39:18.484 | 13:39:21.407 | **~2.9s** | Instance decision LLM: 2.9s |
| **Step 2.5** (Sync) | 13:39:21.408 | 13:39:21.419 | **~11ms** | DB writes |
| **Step 3** (Context build) | 13:39:21.419 | 13:39:21.432 | **~13ms** | Data gathering, prompt assembly |
| **Step 3.4** (SDK → 1st token) | 13:39:21.432 | 13:39:26.097 | **~4.7s** | Claude SDK startup + MCP connect + first thinking token |

```
TOTAL TTFT:  ~12.8 seconds

User message received:    13:39:13.317
First thinking WS sent:   13:39:26.097
```

#### Step 1 Sub-Breakdown (New-Topic Case)

| Sub-phase | Duration | Details |
|-----------|----------|---------|
| Continuity LLM call (`gpt-5.1`) | **~1.9s** | Decided "different topic" |
| Embedding generation | **~3ms** | Cache hit |
| EverMemOS search | **~1.1s** | Searched 82 narratives, found 1 candidate |
| LLM judge (`gpt-4o-mini`) | **~2.2s** | Low confidence score (0.33) → LLM matched to default narrative |

#### Post-Response Timing (Not Affecting TTFT)

| Step | Duration | Details |
|------|----------|---------|
| **Step 3** (full agent loop) | ~4m19s | Multiple tool calls (PDF download, Python exec, web search) |
| **Step 4** (Persist) | ~266ms | Trajectory file, event update, narrative update |
| **Step 5** (Hooks) | ~10s | EverMemOS write (~10s), chat persist (~10ms) |

---

## 4. TTFT Breakdown Comparison

### 4.1 Absolute Durations

| Component | Sample A (same topic) | Sample B (new topic) |
|-----------|----------------------|---------------------|
| Step 0 (init) | 15ms | 7ms |
| Step 1 — continuity LLM | **4.3s** | **1.9s** |
| Step 1 — EverMemOS search | — | **1.1s** |
| Step 1 — LLM judge | — | **2.2s** |
| Step 1.5 (markdown) | 1ms | 1ms |
| Step 2 — instance decision LLM | **2.9s** | **2.9s** |
| Step 2.5 (sync) | 26ms | 11ms |
| Step 3 (context build) | 24ms | 13ms |
| Step 3.4 (Claude SDK TTFT) | **8.7s** | **4.7s** |
| **TOTAL TTFT** | **16.2s** | **12.8s** |

### 4.2 Percentage Breakdown

| Component | Sample A | Sample B |
|-----------|----------|----------|
| Step 1 (all narrative work) | 27% | 41% |
| Step 2 (module decision) | 18% | 23% |
| Step 3.4 (Claude SDK) | 54% | 37% |
| DB/IO/other | 1% | <1% |

### 4.3 Key Observations

1. **LLM calls dominate the TTFT-critical path**: 2-3 sequential LLM calls consume 45-63% of TTFT before the main agent loop even starts.
2. **DB and file I/O are negligible**: Total across all steps is <300ms.
3. **Claude SDK TTFT is variable**: 4.7s vs 8.7s — depends on prompt size (~18-21KB system prompt), API load, and MCP handshake time.
4. **The same 4 modules are loaded every time**: The ~3s LLM call always returns the same result.

---

## 5. Current LLM Model Usage

| Component | Model | Purpose | Typical Latency |
|-----------|-------|---------|-----------------|
| Step 1 — Continuity detection | **`gpt-5.1-2025-11-13`** | Yes/no: "same topic?" | 1.9–4.3s |
| Step 1 — Narrative LLM judge | **`gpt-4o-mini`** | Match query to narrative | ~2.2s |
| Step 2 — Instance decision | **`gpt-5.1-2025-11-13`** | Which modules to load | 2.4–2.9s |
| Step 3.4 — Main agent loop | **Claude (via Agent SDK)** | Reasoning + tool calling | Variable |
| Step 4 — Dynamic summary | **`gpt-4o-mini`** | Summarize event | Post-response |
| Step 5 — Social extraction | **LLM (varies)** | Extract entities | Post-response |

**Config locations:**
- `gpt-5.1` hardcoded in: `src/xyz_agent_context/agent_framework/openai_agents_sdk.py` (line 37)
- `gpt-4o-mini` configured in: `src/xyz_agent_context/narrative/config.py` (lines 34-36)

---

## 6. Module Loading Analysis

### 6.1 Modules Loaded Per Run (from 6 production logs)

Every single run loads the **same 4 modules**:

| Module | Type | How Loaded | Has MCP |
|--------|------|------------|---------|
| **AwarenessModule** | Capability | Auto (agent-level) | Yes (7801) |
| **ChatModule** | Capability | Auto (narrative-level) | Yes (7804) |
| **SkillModule** | Always-load | Auto (hardcoded) | No |
| **JobModule** | Virtual | Auto (fallback ensure) | Yes (7803) |

**Result**: `execution_path=agent_loop` in 100% of cases.

### 6.2 Step 2 Timing Breakdown

| Sub-step | Duration | Evidence |
|----------|----------|---------|
| Load current instances from DB | ~3ms | `_load_current_instances`: instant |
| **LLM instance decision** | **2.4–2.9s** | `llm_decide_instances`: the bottleneck |
| Module object creation (4 objects) | **<1ms** | `_create_module_objects`: Python instantiation only |
| MCP servers | 0ms | Already running as separate processes at boot |

**Conclusion**: 99.9% of Step 2 time is the LLM call. Module creation is negligible.

---

## 7. Proposed Optimizations

### 7.1 Step 1 — Continuity Detection: Use Smaller Model

**Current**: `gpt-5.1-2025-11-13` (frontier model) for a simple binary classification.
**Proposed**: Switch to `gpt-4o-mini` or `gpt-4.1-nano`.

**Rationale**: Continuity detection is a straightforward yes/no decision: "does this query belong to the current narrative?" This doesn't need a frontier model. The LLM judge in the same step already uses `gpt-4o-mini` successfully.

**Expected savings**: ~1–3s (reducing from 2-4s to <1s)
**Effort**: Low — change model string in `openai_agents_sdk.py` or add a config parameter.
**Risk**: Low — the decision is simple enough for a small model. Can A/B test accuracy.

### 7.2 Step 2 — Skip LLM, Always Load All Modules

**Current**: `gpt-5.1` LLM call (~2.5-3s) to decide which modules to load.
**Proposed**: Skip the LLM call entirely; always load all 4 capability modules.

**Rationale**: In 100% of observed production runs, the same 4 modules are loaded. The LLM decision adds no value in the current module configuration. The only variable would be task-level modules (e.g., creating a new JobModule instance for a specific job), but the current logs show 0 task modules ever being created through the LLM decision path.

**Expected savings**: ~2.5–3s
**Effort**: Medium — add a bypass flag or config option in `loader.py` to skip `llm_decide_instances()` and directly load all capability modules + virtual JobModule.
**Risk**: Medium — if new modules are added in the future, this bypass would need to be revisited. Mitigate by making it a configurable option (`SKIP_MODULE_DECISION_LLM=true`).

### 7.3 Summary of Projected Savings

| Optimization | Current | Projected | Savings |
|-------------|---------|-----------|---------|
| Step 1 continuity: smaller model | 1.9–4.3s | 0.3–1.0s | **~1.5–3s** |
| Step 2: skip LLM, load all | 2.4–2.9s | ~3ms | **~2.5–3s** |
| **Combined** | | | **~4–6s** |

**Projected TTFT after optimizations:**

| Scenario | Current TTFT | After Optimizations | Improvement |
|----------|-------------|--------------------:|-------------|
| Same-topic | 16.2s | ~10–12s | 25–38% faster |
| New-topic | 12.8s | ~7–9s | 30–45% faster |

---

## 8. Items Not Addressed (Future Work)

These are noted but not actionable right now:

| Item | Current Impact | Notes |
|------|---------------|-------|
| **Claude SDK TTFT** (4.7–8.7s) | 37–54% of TTFT | Depends on prompt size, API latency, MCP handshake. Could investigate: reducing system prompt size, pre-warming MCP connections, or prompt caching. |
| **EverMemOS search** (~1.1s) | Only on new topics | Cannot change now per user direction. |
| **Step 1 LLM judge** (~2.2s) | Only on low-confidence matches | Already using `gpt-4o-mini`. Could potentially be parallelized with other work. |
| **Step 5 hooks** (~10s) | Post-response, not TTFT-affecting | EverMemOS write is slow but doesn't block user. |
| **Parallelizing Step 1 + Step 2** | Could save ~2-3s | If continuity + module decision are independent, run them concurrently. More complex architectural change. |

---

## 9. Next Steps

1. **Build speed test instrumentation** — Add structured timing logs to each step so we can measure improvements consistently across multiple runs.
2. **Implement Step 1 model switch** — Change continuity detection to `gpt-4o-mini` or `gpt-4.1-nano`, then benchmark.
3. **Implement Step 2 bypass** — Add configurable flag to skip module decision LLM, then benchmark.
4. **Collect baseline metrics** — Run N=10+ conversations across both scenarios (same-topic, new-topic) before and after changes.
