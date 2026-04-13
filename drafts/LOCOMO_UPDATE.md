# LocoMo Benchmark Update

**Owner**: @Xiangchao Chen

**Source**:
- Meeting notes: Mar 3, Mar 6, Mar 10 Sync on NexusAgent Benchmark
- LocoMo benchmark test repo (Xiangchao's branch)

---

## Early Exploration

Investigated LocoMo benchmark structure and evaluation metrics. Key challenge: LocoMo tests long-conversation memory with time-sensitive questions across multi-session dialogs. Unlike GAIA (single-turn Q&A), LocoMo requires the agent to maintain conversation history, correctly segment topics into narratives, and retrieve relevant memories for QA.

Initial approach: Xiangchao completed Dialog 0's first topic segmentation and upload. Two test runs showed poor overall scores. Adopted a 3-tier answer extraction approach (direct response → regex fallback → GPT extraction), with LLM extraction yielding best results.

---

## Nexus Agent Runs

**First Run Results**: Scores poor overall. Two major issues identified:

1. **Agent interactive mode conflicts with LocoMo evaluation** — Claude's extended thinking and long narrative content injection cause agent responses to ignore format constraints, even with explicit formatting instructions
2. **Answer extraction pipeline issues** — MCP tool calls returning empty content due to rate limit cache event placement bug (fixed Mar 6)

---

## Core Issues Identified

| Issue | Description | Status |
|---|---|---|
| **Narrative snowball/generalization** | Overlapping topics cause narratives to broaden indefinitely (e.g., 200+ dialogs lumped into one narrative). QA returns "no information available" | In progress — 4 approaches being tested |
| **Continuity detector over-grouping** | LLM-based continuity detector assigns too many dialogs to same narrative; information too dense, memory retrieval fails | Ablation experiments planned |
| **Time information handling** | Dates only injected at topic split point; date changes across sessions not tracked; dates not preserved as key memory. LocoMo evaluation is time-sensitive | Not yet resolved |
| **EverMemOS retrieval accuracy** | Unclear if retrieved info is properly incorporated or being diluted/overridden by narrative content | Verification needed |
| **MCP empty responses** | Pipeline bug causing empty tool responses | Fixed (rate limit cache bug) |

---

## Testing Approaches

Two distinct approaches have been discussed for LocoMo benchmark testing:

### Approach 1: Full Pipeline Replay (Current)

Replay each dialog chunk through the **entire NexusAgent pipeline** sequentially, simulating a real user chatting:

```
Dialog chunk → Continuity Detector → Narrative assignment →
EverMemOS indexing → Memory module → ... → Next chunk
                    ... (repeat for all chunks) ...
QA questions → Agent retrieves from whatever was built
```

- Each chunk goes through narrative selection (continuity LLM + judge LLM)
- System decides which narrative to assign it to, creates new ones as needed
- EverMemOS indexes the content as part of the narrative flow
- Then QA questions are asked at the end, agent retrieves from whatever was built

**Pro**: Tests the full system end-to-end, closest to real user experience
**Con**: Narrative snowball problem compounds — once continuity detector mis-groups early chunks, all subsequent chunks inherit the error. Hard to tell if QA failures are from bad retrieval or bad narrative construction.

### Approach 2: Decoupled Memory-First (Proposed by Xiong, Mar 10)

Separate memory indexing from narrative construction:

```
Step 1: Batch-inject ALL dialog data → EverMemOS directly (skip narrative pipeline)
Step 2: Build narrative + summaries from indexed memory after the fact
Step 3: QA retrieval pulls from both memory system and constructed narratives
```

- EverMemOS gets clean, complete data first — no dependency on continuity detector decisions
- Narrative construction happens as a second pass, informed by the full memory index
- Can leverage EverMemOS cluster summaries for global correction

**Pro**: Isolates retrieval quality from narrative quality — can diagnose which component is failing. If QA works well with direct memory but poorly with narratives, the problem is clearly in narrative construction, not retrieval.
**Con**: Doesn't reflect real production flow; needs separate pipeline to be built.

### Why This Distinction Matters

The team currently can't tell whether bad LocoMo scores come from:
- (A) EverMemOS failing to retrieve the right info, or
- (B) Narratives being too broad/diluted, burying the info

Approach 2 answers that question directly. If scores improve significantly with direct memory injection, it confirms the narrative system is the bottleneck — which aligns with the snowball problem observed in testing.

---

## Narrative Generalization — Solution Approaches (as of Mar 10)

Xiangchao is testing 4 approaches to address the core narrative snowball problem:

1. **Remove overlapping topics** — Split sentences containing multiple topics into independent topic sentences
2. **Compute topic purity** — Calculate ratio of topic belonging to its assigned narrative; if purity is low, re-split using model
3. **Add event count penalty** — Penalize narratives with too many events to limit scope expansion
4. **Dynamic threshold adjustment** — Adapt retrieval count based on context

Code pushed to new GitHub branch. Replay testing not yet completed.

**Known risks**: Core embedding drift over time; purity-based splitting may lose partial information; current test results still show gaps.

---

## Architecture Decisions (from Xiong/Boss)

- **Priority**: Narrative generalization fix + benchmark validation first
- **Deferred**: Tool overhead optimization, memory retrieval tuning, dynamic retrieval count adjustment
- **Proposed decoupling**: Batch-inject dialog data into EverMemOS first, then retrieve associations + summaries — separating memory indexing from narrative construction (Approach 2 above)
- **Global correction**: Build correction mechanism based on memory system's cluster summaries to fix local narrative decision errors

---

## Next Steps

1. Complete overlapping topic removal testing and run benchmark validation
2. Test all 3 remaining generalization approaches, sync results with Hongyi Gu and Xiong
3. Verify EverMemOS retrieval info is being properly used in QA context
4. Run ablation experiments: normal continuity detector vs forced non-continuous vs forced continuous topics
5. Prepare benchmark status document for Thursday sync with Michael
6. After Arena project support wraps up, deep-dive on generalization + time handling fixes

---

## Appendix and File Links

- Meeting notes: Mar 3, Mar 6, Mar 10 (in downloads)
- LocoMo test results: *(add link to Xiangchao's test output when available)*
- Narrative generalization branch: *(add GitHub branch link)*
