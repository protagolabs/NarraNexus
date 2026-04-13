# Narrative & Memory System Review Report
# Narrative与记忆系统审查报告

> Author: Hongyi Gu
> Date: 2026-04-02
> Source: Code review (2026/03/24-03/31), Team discussion (2026/04/01), LoCoMo benchmark findings
> Related docs: 2026/03/30 Narrative Review, Meeting notes 2026/04/01

---

## 1. Background 背景

Through the LoCoMo benchmark test (long-term conversational memory), we found that NarraNexus has significant issues in long conversation scenarios: narrative snowballing (200+ dialogs lumped into one narrative), continuity detector over-grouping, unclear EverMemOS retrieval accuracy, and time information loss. These point to fundamental design questions about how narrative, EverMemOS, and ChatModule memory work together.

通过LoCoMo benchmark测试（长期对话记忆），我们发现NarraNexus在长对话场景中存在显著问题：narrative滚雪球（200+对话归入一个narrative）、连续性检测器过度聚合、EverMemOS检索准确性不明确、时间信息丢失。这些指向了narrative、EverMemOS和ChatModule记忆如何协作的根本性设计问题。

A full code review was conducted on the narrative retrieval pipeline, covering every function from session management to EverMemOS search to LLM judgment. This report summarizes the findings, team consensus, and action items.

对narrative检索pipeline进行了完整的代码审查，覆盖从session管理到EverMemOS搜索到LLM判断的每个函数。本报告总结发现、团队共识和行动项。

---

## 2. System Architecture 系统架构

### 2.1 Core Concepts 核心概念

| Component 组件 | Role 角色 | EN | CN |
|---|---|---|---|
| **Narrative** | Topic-level routing container | Holds metadata (name, summary, keywords, embedding, actors). Links to module instances. Does NOT store conversation content. | 话题级路由容器。持有元数据，链接模块实例。不存储对话内容。 |
| **ChatModule Memory** | Raw memory layer (底层) | Per (user_id, narrative_id) conversation transcript. Immediate write, exact text. Frontend chat UI source. | 按(user_id, narrative_id)的原始对话记录。即时写入，精确文本。前端UI数据源。 |
| **EverMemOS** | Semantic memory layer (中间层) | Processes raw messages into episodes (boundary detection → summarization → embedding). RRF retrieval (BM25 + vector). | 将原始消息处理成episode（边界检测→摘要→向量化）。RRF检索。 |
| **Native VectorStore** | Narrative-level vector cache | In-memory cosine search on routing_embedding. Fallback when EverMemOS unavailable. | 内存中routing_embedding余弦搜索。EverMemOS不可用时的后备。 |

### 2.2 Data Stores 数据存储

| Store | Location | Keyed by | Content | Written |
|---|---|---|---|---|
| Narrative | MySQL | narrative_id | name, summary, keywords, routing_embedding, actors, event_ids | After every turn (Step 4.4) |
| ChatModule | MySQL | instance_id = (user_id, narrative_id) | Raw message pairs [{role, content}] | After every turn (Step 5 hook) |
| EverMemOS | MongoDB + ES + Milvus | group_id (=narrative_id) + user_id | Episodes: summaries + embeddings | After every turn (Step 5 hook, async) |
| Session | JSON file | (agent_id, user_id) | current_narrative_id, last_query/response, timeout 600s | After narrative selection |

### 2.3 EverMemOS Internal Hierarchy EverMemOS内部层级

```
Raw messages → MemCell (boundary-detected chunk, multiple messages)
                  → Episode (LLM-summarized narrative of MemCell)
                  → Cluster (groups MemCells by semantic similarity + 7-day temporal, within same group)
                  → Group (= narrative_id)
```

---

## 3. Key Questions Discussed 讨论的核心问题

### 3.1 EverMemOS与NarraNexus的关系 / Relationship between EverMemOS and NarraNexus

#### Q1: Scope差异 — Narrative vs Episode vs Cluster
#### Q1: Scope difference — Narrative vs Episode vs Cluster

| Level | Scope | Time span |
|---|---|---|
| Episode | Continuous messages on one micro-topic | Minutes |
| Cluster | Episodes grouped by semantic similarity + recency | Days (7-day max) |
| Narrative | Should be > cluster — an ongoing project/goal/topic | Weeks/months |

**Problem 问题:** Currently a single query can create a narrative, making its scope as small as one episode. Narrative and episode overlap conceptually.

**当前问题：** 一个新query就能创建narrative，scope可以和episode一样小。Narrative和episode在概念上重叠。

**Team consensus 团队共识:** Narrative scope needs to be larger than cluster. Current eager creation is a problem. Options: (a) improve LLM creation quality, (b) delay narrative creation — accumulate in general narrative first, promote after sustained topic. Specific scope definition (lifetime period vs lifetime goal) to be determined through experiments.

**团队共识：** Narrative scope需要大于cluster。当前过度创建是问题。选项：(a) 提升LLM创建质量，(b) 延迟创建——先归入通用narrative，持续话题后再拆分。具体scope定义通过实验确定。

#### Q2: Retrieval logic违和感 — Middle-out selection
#### Q2: Retrieval logic awkwardness — Middle-out selection

**Current flow 当前流程:** Query → EverMemOS episode search (middle layer) → aggregate by narrative (top layer) → load ChatModule history (bottom layer)

**Problem 问题:** This is neither top-down (topic → sub-topic → detail) nor bottom-up (relevant memories → aggregate to topic). It's middle-out: episode scores select the narrative, then episodes are partially discarded.

**当前既不是top-down（话题→子话题→细节）也不是bottom-up（相关记忆→聚合到话题），而是middle-out：episode分数选择narrative，然后episode被部分丢弃。**

**What Narrative uniquely provides 当前Narrative独特提供的：**
- Actor/participant information (who is involved) 参与者信息
- Job linkage (diluted utility) Job关联（效用稀释）
- High-level summary metadata 高层摘要元数据
- Orchestration (module instances, active state) 编排（模块实例、活跃状态）

**What it does NOT provide over EverMemOS episodes 相比EverMemOS episodes未额外提供的：**
- More memory content 更多记忆内容
- Better retrieval signal 更好的检索信号

**Team consensus 团队共识:** Narrative retrieve logic (top-down / bottom-up / mixed) is a team decision. Current approach needs improvement. Suggested: narrative centroid embedding + EverMemOS episode search as two-dimensional mixed retrieval.

#### Q3: ChatModule memory vs EverMemOS episodes
#### Q3: ChatModule记忆 vs EverMemOS episodes

**Current situation 现状:** Both provide long-term memory, but through different paths:
- Continuity path (most common): only ChatModule DB (last 40 messages)
- Non-continuous path: EverMemOS episode_contents
- They are mutual fallbacks, not layered 它们互为fallback，而非分层

**Problem 问题:** ChatModule is raw message layer, EverMemOS is semantic layer. They overlap in function but differ in nature. Current design treats them as interchangeable fallbacks rather than complementary layers.

**ChatModule目前的不可替代作用：**
- Short-term memory (15 recent messages from other narratives) — only source
- Continuity path long-term memory — only source when EverMemOS not loaded
- Frontend chat display — only source
- Immediate availability — no processing delay

**Team consensus 团队共识:** 
- Short-term: EverMemOS should be the optional plugin, not the core. System must work without it. (梁斌核心观点)
- Long-term: Flatten the information layer — migrate EverMemOS unique capabilities (episode summarization, RRF retrieval) to ChatModule or Narrative, weakening EverMemOS dependency until it can be removed painlessly.
- 短期：EverMemOS作为可选插件，不能承担核心。系统必须在没有EverMemOS时正常运行。
- 长期：拉平信息层级——将EverMemOS独特能力迁移到ChatModule或Narrative，弱化EverMemOS依赖。

### 3.2 Narrative自身设计 / Narrative Design

#### Q4: Narrative间是否需要link / Whether narratives need links

**Decision 决定:** Short-term NO link. 短期不加link。

**Principle established 确立原则：** Narrative content relatively independent
- Memory loading不需要跨narrative去重
- Auxiliary由每个narrative content独立决定
- Context控制更简单

**Future consideration 后续考虑：** Link design as experimental content, tested through scope experiments later.

#### Q5: Narrative summary无限增长 / Unbounded summary growth

**Problem 问题:** `current_summary` grows with every turn, no compaction. Affects: continuity detection input, LLM judge input, embedding quality, context window budget.

**Team consensus 团队共识:**
- Need background consolidation mechanism (后台整理机制)
- 梁斌 proposed: daytime interaction period (incremental update) → nighttime consolidation period (merge, split, dynamic memory update)
- `description` field can be removed, keep only `current_summary`
- Summary needs internal limit and self-compaction

#### Q6: Narrative与Social Network联动 / Narrative-Social Network integration

**Current problem 当前问题:** Participant info in LLM judgment uses only `topic_hint[:50/100]` — extremely thin. Social Network has rich entity information (role, description, contact) but it's not used.

**Team consensus 团队共识:** Decouple narrative from social network. Narrative only stores actor IDs, detailed entity info lives in Social Network. Optimize information flow through iteration.

---

## 4. Confirmed Bugs 确认的Bug

| Bug | Severity | Description EN | Description CN | Status |
|---|---|---|---|---|
| **EverMemOS no agent_id isolation** | High | Search uses only user_id. Cross-agent episodes pollute ranking. Client-side post-filter is a hack (top_k×3). | 搜索只用user_id，跨agent的episode污染排名。客户端后过滤是hack。 | Must fix (梁斌确认) |
| **LLM judge truncation at call site** | High | Summary truncated to 300 chars, episodes to 500 chars, participant to 50/100 chars — all at the LLM call location, not managed by data source internally. | 摘要截断300字符，episode截断500字符，participant截断50/100字符——都在LLM调用处截断，不是数据源内部管理。 | Must fix |
| **Continuity path missing EverMemOS** | Medium | When continuity passes, evermemos_memories is empty. Agent only sees ChatModule DB (last 40 messages), no semantic episodes. | 连续性通过时evermemos_memories为空。Agent只看到ChatModule DB最近40条消息。 | Fix aligned with "context来源应该一样" principle |
| **Job trigger missing EverMemOS** | Medium | forced_narrative skips EverMemOS entirely. Same gap as continuity path. | forced_narrative完全跳过EverMemOS。 | Same fix |

---

## 5. Established Principles 确立的原则

### P1: Context来源一致 / Consistent context sources across all paths
Job trigger, continuous, non-continuous should use the same layered context:
- Top level: Narrative metadata
- Middle layer 1: Cluster (optional, from EverMemOS)
- Middle layer 2: Episode (from EverMemOS)
- Bottom layer: Short-term memory + detailed events (from ChatModule)

### P2: 长度管理交到内部 / Length management at source, not at call site
No truncation at LLM call locations. Each data source provides bounded output internally.
- Narrative provides a self-compacted summary within limit
- Episodes provide bounded summaries
- Short-term memory has token budget (currently 40k chars ≈ 10k tokens)

### P3: Narrative互相independent / Narratives are independent
- No cross-narrative dedup needed in memory loading
- Auxiliary priority determined solely by each narrative's own content
- No link-based transitive loading

### P4: Narrative needs internal limits / Narrative需要内部限制
- Topic scope has a lower bound (larger than cluster)
- Summary has a size bound (self-compacting)
- Each narrative provides bounded context contribution

### P5: EverMemOS as optional plugin / EverMemOS作为可选插件
- System must work without EverMemOS
- Long-term: migrate unique capabilities to core modules
- Consider MOS fallback when not started (context来源调整对系统无本质影响)

---

## 6. Action Items 行动项

### Immediate (Bug fixes) 立即修复

| Item | Owner | Description |
|---|---|---|
| Agent_id isolation in EverMemOS | Hongyi + EverMemOS team | Add agent_id as first-class filterable field (~25 files in EverMemOS). Scoped in NARRATIVE_ACTION_PLAN.md |
| Remove LLM-site truncation | Hongyi | Remove 300/500/50/100 char truncations in retrieval.py. Data sources should self-limit. Partially done on feature branch. |
| Align continuity path context | Hongyi + Bin | Load EverMemOS episodes on continuity path (pass query_text to _search). Same for Job trigger path. |

### Short-term (Principle alignment) 短期对齐

| Item | Owner | Description |
|---|---|---|
| Narrative summary compaction | Hongyi + Bin | Add compaction mechanism in narrative updater. When summary exceeds threshold, LLM compacts it. Remove `description` field, keep only `current_summary`. |
| Background consolidation mechanism | Bin + Xiong | 后台整理机制：narrative合并与拆解、动态更新memory。结合agent可演化机制。 |
| Narrative centroid table | Hongyi | New `narrative_centroids` table for incremental event embedding averages. Additional ranking signal. Implemented on feature branch. |
| Context budget allocation | Hongyi | Define token budgets per context section (narrative summary, episodes, short-term memory). Replace ad-hoc truncation with structured limits. |

### Medium-term (Architecture) 中期架构

| Item | Owner | Description |
|---|---|---|
| EverMemOS dependency reduction | Hongyi + Bin + Xiong | Flatten information layer. Migrate episode summarization + RRF retrieval to core modules. Weaken EverMemOS until removable. |
| Narrative retrieve logic redesign | Team | Decide top-down / bottom-up / mixed. Implement narrative centroid + EverMemOS two-dimensional mixed retrieval. |
| Narrative scope experiments | Team | Test different scope definitions through LoCoMo benchmark re-runs. Measure effect of delayed creation, larger scope, etc. |
| Social Network integration optimization | Hongyi + Bin | Decouple narrative from social network. Optimize actor info flow. |

### Evaluation 效果评估

| Item | Owner | Description |
|---|---|---|
| Log documentation | Hongyi | Collect detailed logs for narrative mechanism analysis. Coordinate with Bin for root cause analysis. |
| LoCoMo re-run | Xiangchao + Hongyi | After bug fixes, re-run LoCoMo to evaluate true upper bound (current scores reflect pipeline failures, not agent capability). |

---

## 7. Narrative Dynamic Update Mechanism (Proposed)
## 7. Narrative动态更新与整理机制（提议）

Based on meeting discussion (梁斌 proposal):

```
Phase 1: Daytime Interaction 白天交互期
  - Incremental narrative update (every turn)
  - Only add new information 仅查看新增量信息
  - Real-time conversation processing 实时处理交互流量

Phase 2: Nighttime Consolidation 夜间整理期
  - Full information consolidation 全局信息整理
  - Narrative merge and split 合并与拆解
  - Dynamic memory update 动态更新memory
  - Consolidate call code/stream mechanism 实化call code和stream机制

Phase 3: Long-term Evolution 长期演化
  - Periodic narrative definition optimization 定期优化narrative定义
  - Adjust merge/split frequency 调整合并拆解频率
  - Integrate with actual iteration pipeline 结合实际迭代pipeline
```

Core idea 核心：通过分时段处理实现搜索效率与深度优化的平衡，提升agent记忆管理的灵活性。

---

## 8. Reference Files 相关文档

| Document | Location |
|---|---|
| Narrative Review (original) | 2026/03/30 Narrative review |
| Meeting Notes | 智能纪要 2026年4月1日 |
| Detailed Function Trace | drafts/narrative_pipeline/NARRATIVE_FUNCTION_TRACE.md |
| Data Lifecycle | drafts/narrative_pipeline/NARRATIVE_DATA_LIFECYCLE.md |
| Big Picture Analysis | drafts/narrative_pipeline/NARRATIVE_BIG_PICTURE.md |
| Action Plan (technical) | drafts/narrative_pipeline/NARRATIVE_ACTION_PLAN.md |
| Runtime Pipeline | drafts/runtime_pipeline/RUNTIME_PIPELINE.md |
