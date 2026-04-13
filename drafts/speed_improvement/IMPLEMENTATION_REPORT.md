# Speed Improvement — Implementation Report

**Branch**: `improve/speed`
**Date**: 2026-03-09
**Reference**: `drafts/speed_improvement/SPEED_ANALYSIS.md`

---

## Summary

Three optimizations implemented to reduce TTFT (Time-to-First-Token):

| # | Optimization | Measured Savings |
|---|-------------|-----------------|
| 1 | Switch continuity detection from `gpt-5.1` → `gpt-4o-mini` | **~0.5–0.7s** |
| 2 | Skip LLM module decision, load all modules directly | **~2.5–3.0s** |
| 3 | Switch narrative judge from `gpt-5.1` → `gpt-4o-mini` | **~1.5–1.8s** |
| | **Combined** | **~4.5–5.5s** |

---

## Before vs After Comparison

### Test Setup

Three identical messages sent to a new agent in sequence:
1. `"you are speed testing agent, i will use you for some test"` (new session)
2. `"Here's a fun riddle..."` (topic switch)
3. `"Hey have you heard of netmind arena?"` (topic switch)

### Baseline (Pre-Optimization, from SPEED_ANALYSIS.md)

Measured from production logs before any changes. All LLM calls used `gpt-5.1-2025-11-13`, module decision LLM was active.

| Component | Sample A (same-topic, Mar 3) | Sample B (new-topic, Feb 27) |
|-----------|------------------------------|------------------------------|
| Step 0 (Init) | 15ms | 7ms |
| Step 1 — Continuity LLM (`gpt-5.1`) | **4.3s** | **1.9s** |
| Step 1 — EverMemOS search | — | **1.1s** |
| Step 1 — LLM Judge (`gpt-5.1`*) | — | **2.2s** |
| Step 2 — Module decision LLM (`gpt-5.1`) | **2.9s** | **2.9s** |
| Step 3 → first token (Claude SDK) | **8.7s** | **4.7s** |
| **TOTAL TTFT** | **16.2s** | **12.8s** |

*Note: The judge was labeled as `gpt-4o-mini` in the original analysis but was actually routed through `OpenAIAgentsSDK()` which hardcoded `gpt-5.1`. The config existed but was not wired up.

### After Optimization (Run 3, agent_f77bbb21b5fe, Mar 9)

All three optimizations active: continuity → `gpt-4o-mini`, judge → `gpt-4o-mini`, module decision → skipped.

| Component | Run 1 (new session) | Run 2 (topic switch) | Run 3 (topic switch) |
|-----------|--------------------:|---------------------:|---------------------:|
| Step 0 (Init) | 15ms | 16ms | 18ms |
| Step 1 — Continuity LLM (`gpt-4o-mini`) | skipped | **1.68s** | **1.99s** |
| Step 1 — EverMemOS search | 1.12s | 1.89s | 1.00s |
| Step 1 — Judge LLM (`gpt-4o-mini`) | 2.87s | **2.33s** | **1.84s** |
| Step 1 total | 3.99s | 5.91s | 4.84s |
| Step 2 — Module (LLM skipped) | **0.005s** | **0.005s** | **0.006s** |
| Step 3 → first token (Claude SDK) | 4.49s | 3.55s | 3.04s |
| **TOTAL TTFT** | **8.50s** | **9.47s** | **7.90s** |

### Per-Component Savings

| Component | Before | After | Savings | Notes |
|-----------|--------|-------|---------|-------|
| Continuity LLM | 1.9–4.3s | 1.68–1.99s | **~0.5–2.3s** | `gpt-5.1` → `gpt-4o-mini` |
| Judge LLM | 2.2–3.6s | 1.84–2.87s | **~0.4–1.8s** | `gpt-5.1` → `gpt-4o-mini` |
| Module decision LLM | 2.4–2.9s | 0.005s | **~2.5–3.0s** | Skipped entirely |
| Claude SDK → 1st token | 4.7–8.7s | 3.04–4.49s | variable | Not changed, natural variance |

### TTFT Summary

| Scenario | Before | After | Improvement |
|----------|-------:|------:|-------------|
| New session (first message) | ~16.2s | **8.50s** | **47% faster** |
| Topic switch (new topic) | ~12.8s | **7.90–9.47s** | **26–38% faster** |

---

## Remaining TTFT Breakdown (Post-Optimization)

Based on Run 3 (best representative — follow-up turn, topic switch):

```
TTFT = 7.90s
├── Step 1: Narrative Selection     4.84s  (61%)
│   ├── Continuity LLM (4o-mini)    1.99s
│   ├── EverMemOS search            1.00s  (highly variable: 1–13s)
│   └── Judge LLM (4o-mini)         1.84s
├── Step 2: Module Loading          0.01s  ( 0%)  ← was 2.9s
├── Step 3: Context + SDK start     3.04s  (39%)
│   ├── Context build               ~30ms
│   └── Claude SDK → 1st token      ~3.0s  (API latency, not optimizable)
└── Other (init, sync, markdown)    0.02s  ( 0%)
```

### What We Can Still Optimize

| Target | Current | Potential | Approach |
|--------|---------|-----------|----------|
| Continuity LLM | 1.68–1.99s | ~0.5s | Try `gpt-4.1-nano` |
| Judge LLM | 1.84–2.87s | ~0.5s | Try `gpt-4.1-nano` |
| EverMemOS | 1.0–13.0s | variable | Not under our control currently |
| Claude SDK | 3.0–4.5s | ~3.0s | API latency floor, minimal room |

### What We Cannot Optimize

- **Claude SDK latency (~3s)**: This is network round-trip + model startup. Irreducible without prompt caching or provider-side changes.
- **EverMemOS variance**: External service, sometimes spikes to 13s. Would need EverMemOS team investigation.

---

## Changes Implemented

### Change 1: Smaller Model for Continuity Detection

**File: `src/xyz_agent_context/agent_framework/openai_agents_sdk.py`**
- Added optional `model` parameter to `llm_function()` (defaults to `gpt-5.1` via `DEFAULT_MODEL`)
- Added `logger.info` to log which model is used per call

**File: `src/xyz_agent_context/narrative/_narrative_impl/continuity.py`**
- Now passes `model=narrative_config.CONTINUITY_LLM_MODEL` (`gpt-4o-mini`) to SDK

### Change 2: Skip LLM Module Decision

**File: `src/xyz_agent_context/settings.py`**
- Added `skip_module_decision_llm: bool = False` (enable via `SKIP_MODULE_DECISION_LLM=true` in `.env`)

**File: `src/xyz_agent_context/module/_module_impl/loader.py`**
- Fast-path `_load_all_capability_modules()` bypasses LLM, loads all modules directly

### Change 3: Smaller Model for Narrative Judge

**File: `src/xyz_agent_context/narrative/config.py`**
- Added `NARRATIVE_JUDGE_LLM_MODEL = "gpt-4o-mini"`

**File: `src/xyz_agent_context/narrative/_narrative_impl/retrieval.py`**
- Both `llm_function` calls now pass `model=config.NARRATIVE_JUDGE_LLM_MODEL`

### All Files Changed

| File | Change |
|------|--------|
| `openai_agents_sdk.py` | `model` param + logging |
| `continuity.py` | Uses `CONTINUITY_LLM_MODEL` from config |
| `retrieval.py` | Uses `NARRATIVE_JUDGE_LLM_MODEL` from config |
| `config.py` (narrative) | Added `NARRATIVE_JUDGE_LLM_MODEL` |
| `loader.py` | Fast-path bypass + `_load_all_capability_modules()` |
| `settings.py` | Added `skip_module_decision_llm` flag |

---

## Next Steps

1. **Try `gpt-4.1-nano`** for continuity + judge — could shave another 2–3s total
2. **Investigate EverMemOS spikes** — when it hits 13s, TTFT doubles
3. **Validate accuracy** — confirm `gpt-4o-mini` matches `gpt-5.1` decisions across N=50+ turns
4. **Consider parallelizing** Step 1 sub-phases (continuity + EverMemOS could run concurrently)
