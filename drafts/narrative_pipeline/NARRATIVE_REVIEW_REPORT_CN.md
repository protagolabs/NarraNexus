# Narrative与记忆系统审查报告

> 作者: Hongyi Gu
> 日期: 2026-04-02
> 来源: 代码审查 (2026/03/24-03/31), 团队讨论 (2026/04/01), LoCoMo benchmark发现, 认知科学文献调研
> 相关文档: 2026/03/30 Narrative Review, 会议纪要 2026/04/01

---

## 1. 背景

LoCoMo benchmark测试（长期对话记忆）暴露了NarraNexus在长对话场景中的系统性问题：
- Narrative滚雪球 — 200+对话归入一个narrative，信息密度过高导致检索失败
- 连续性检测器过度聚合 — LLM持续判定"属于同一话题"，narrative无限膨胀
- EverMemOS检索准确性不明确 — 检索到的信息是否真正进入了最终回答
- 时间信息丢失 — LoCoMo强调时间敏感问答，但系统缺乏时间维度

对narrative检索pipeline进行了**逐函数**的完整代码审查，并结合认知科学记忆理论进行架构对比分析。

---

## 2. 系统架构概览

### 2.1 核心概念

| 组件 | 定位 | 说明 |
|---|---|---|
| **Narrative** | 话题级路由容器 (Topic-level routing container) | 持有元数据（name, summary, keywords, embedding, actors），链接module instances。**不存储对话内容本身**，是索引而非数据库 |
| **ChatModule Memory** | 原始记忆层 (Raw memory layer) | 按(user_id, narrative_id)的完整对话记录，即时写入。前端Chat UI数据源。短期记忆和continuity path长期记忆的唯一来源 |
| **EverMemOS** | 语义记忆层 (Semantic memory layer) | 将原始消息处理成Episode（边界检测→摘要→向量化），提供RRF检索（BM25+向量）。按group_id(=narrative_id)组织 |
| **Native VectorStore** | Narrative级向量缓存 | 内存中routing_embedding余弦搜索。EverMemOS不可用时的后备。**非EverMemOS的替代品**——只做Narrative级匹配 |

### 2.2 数据存储

| Store | 位置 | 主键 | 内容 | 写入时机 |
|---|---|---|---|---|
| Narrative | MySQL | narrative_id | name, summary, keywords, routing_embedding, actors, event_ids | 每轮更新 (Step 4.4) |
| ChatModule | MySQL | instance_id = (user_id, narrative_id) | 原始消息对 [{role, content}] | 每轮 (Step 5 hook) |
| EverMemOS | MongoDB+ES+Milvus | group_id(=narrative_id) + user_id | Episodes: 语义摘要+向量 | 每轮 (Step 5 hook, async) |
| Session | JSON文件 | (agent_id, user_id) | current_narrative_id, last_query/response, 超时600s | Narrative选择后 |

### 2.3 EverMemOS内部层级

```
原始消息 → MemCell（边界检测的对话chunk，多条消息）
              → Episode（MemCell的LLM摘要版本，含summary + 全文 + 向量）
              → Cluster（同一group内按语义+时间(7天)聚合的MemCell集合）
              → Group (= narrative_id)
```

---

## 3. 认知科学基础与架构对比 (Cognitive Foundation)

### 3.1 四层记忆模型 — Roger Schank的Dynamic Memory (1982)

| Schank层级 | 定义 | 对应我们系统 | 覆盖程度 |
|---|---|---|---|
| **Event Memory** (具体事件记忆) | 特定经历的记忆 | ChatModule原始消息 + EverMemOS Episode | 较好覆盖 |
| **Generalized Event Memory** (泛化事件记忆) | 从多个事件中抽象出的共同特征 | EverMemOS Cluster (按语义聚合的episodes) | 部分覆盖（Cluster存在但不参与检索） |
| **Situational Memory** (情境记忆) | 上下文和规则 | **缺失** — 无情境层表示 | 未覆盖 |
| **Intentional Memory** (意图记忆) | 目标和计划 | **缺失** — Narrative无goal/intention字段 | 未覆盖 |

Schank还提出了**TOPs (Thematic Organization Points)** — 跨领域关联结构，解释为什么听到贸易谈判会联想到象棋。我们系统当前**无跨narrative关联检索机制**。

### 3.2 Conway自传记忆模型 — Self-Memory System (SMS)

```
Conway层级:     Life Story → Lifetime Periods → General Events → Event-Specific Knowledge
我们的系统:      (缺失)     →  Narrative        → Cluster(未用) →  Episode/ChatModule
```

**关键发现：Conway模型至少有3层检索，我们只用了2层（Narrative → Episode）。** Cluster存在但作为后处理分组而非正式检索中间层。Conway模型还支持**直接访问（direct access）**——特定线索可跳过层级直接检索具体记忆。我们应支持双通道：top-down（当前方式）+ bottom-up（全局episode搜索）。

### 3.3 记忆检索理论 — 不只是向量相似度

当前系统仅用向量余弦相似度检索，认知科学识别出多个缺失的关键机制：

| 检索模型 | 核心机制 | 我们缺什么 |
|---|---|---|
| **ACT-R** (Anderson, 1993) | Activation = base-level(recency×frequency) + spreading activation(context) | 我们只有semantic similarity，缺recency, frequency, importance |
| **SAM** (Raaijmakers & Shiffrin, 1981) | 多线索**乘积**组合，不是加和 | 我们用单一embedding相似度 |
| **TCM** (Howard & Kahana, 2002) | 时间上下文作为缓慢漂移的向量 | 我们用10分钟timeout代替时间上下文 |
| **Generative Agents** (Park et al., 2023) | Score = recency × importance × relevance | 我们缺importance评分 |

**建议的检索评分公式（来自认知科学+AI实践）：**
```
Score = α(semantic_similarity) × β(recency_decay) × γ(importance) × δ(access_frequency)
```
使用**乘积组合**（SAM启发）而非加和。

### 3.4 遗忘是特性，不是缺陷

**互补学习系统理论（CLS, McClelland et al., 1995）**解释了为什么大脑需要两个记忆系统：
- **海马体**：快速学习，单次编码（= 我们的ChatModule原始记录）
- **新皮层**：缓慢学习，跨episode提取统计规律（= 我们的EverMemOS Episode摘要）

**我们的双轨设计（ChatModule + EverMemOS）与CLS理论和模糊痕迹理论高度吻合** — 这是架构的真正优势。

**但缺失主动遗忘机制。** 没有衰减函数，旧episode持续与新信息竞争，造成"前摄干扰"。建议：
- 渐进式整合：近期episode保持全部细节，旧episode周期性重摘要到更粗粒度
- 激活衰减：遵循ACT-R幂律公式 `Activation = ln(Σ t_j^(-0.5))`

### 3.5 Narrative应该是活的重构过程，不是文件夹

Bartlett (1932)的"鬼魂战争"实验表明：每次回忆都会改变记忆内容。Schank从静态Script演化为动态MOP和TOP。**Narrative不应是创建时定义的静态容器，而应在每次访问时从组件episode重构。**

当前系统：Narrative = 静态文件夹（创建时定义metadata，之后只做增量更新）
认知科学：Narrative = 每次访问时动态重构的过程

**建议：**
- Narrative metadata在episode添加或长时间未访问后重新生成
- 多维连续性检测（topic, entity, goal, causality, time）替代当前单维度LLM判断
- 支持Narrative合并和拆分

---

## 4. 核心问题分析

### Part A: EverMemOS与当前系统的耦合问题

**核心判断：EverMemOS记忆与当前narrative耦合严重，两者间的对应关系破坏了EverMemOS memory summarization的独立能力。**

#### A1. Scope重叠 — Narrative和Episode/Cluster概念边界模糊

| 层级 | Scope | 时间跨度 |
|---|---|---|
| Episode | 连续消息的语义chunk | 分钟级 |
| Cluster | 按语义+时间聚合的episodes | 天级 (7天max) |
| Narrative | 应该 > Cluster，一个持续的项目/目标/话题 | 周/月级 |

**问题：** 当前一个query就能创建narrative，scope可以和episode一样小。Narrative和episode在概念上重叠。以我们一开始创建narrative就定了scope的方式，可能并不合适。要么LLM创建很好，要么得扩大scope到lifetime级别。

#### A2. 检索逻辑违和 — Middle-out selection

```
理想: Top-down（大到小：life plan → period → general event → specific event）
     或 Bottom-up（相关记忆聚合到话题）

现实: Middle-out（episode得分 → 反推narrative → 加载ChatModule历史）
     从中间层选到高层，然后直接加载底层，跳过了中间层
```

**对比：** EverMemOS episode + short-term memory vs Narrative额外提供了什么？
- 有：actor/participant信息，related job（作用稀释），high-level summary metadata，orchestration（模块实例）
- 没有：更多memory content，更好的检索信号

Narrative在检索中的角色需要重新审视。

#### A3. ChatModule Memory vs EverMemOS Episodes — 定位混淆

**现状：** 两者在long-term memory功能上互为fallback，而非分层配合。
- Continuity path（最常见路径）：仅ChatModule DB最近40条消息，EverMemOS完全不加载
- Non-continuous path：EverMemOS episode_contents作为长期记忆
- 为什么continuous来源只有一个narrative，而non-continuous时有evermemos + 多个narrative？

**ChatModule不可替代的作用：**
- Short-term memory（其他narrative的15条最近消息）— 唯一来源
- Continuity path长期记忆 — EverMemOS未加载时的唯一来源
- 前端Chat UI — 唯一数据源
- 即时可用 — 无处理延迟

**原则问题：** EverMemOS是否应作为核心组件长期存在？维护成本和迭代风险如何？

### Part B: Narrative自身的设计问题

#### B1. Narrative的Scope和边界

**现状：** Narrative有insert, update, 但没有reconstruct。搭配近乎无上限的summary。

**需要决定的问题：**
1. Narrative的scope应该如何设置 — 以cognitive中的概念，应为lifetime period还是lifetime goal？
2. Narrative之间的boundary如何清晰
3. 由前两个如何调整Narrative中的fields（例如当前的description是否去掉？）
4. 内部是否需要定时自行整理 — 跨narrative; merge, divide not only incremental

#### B2. Narrative间是否需要Link

**当前决定：短期No link。**

**确立原则：** Narrative content relatively independent
- Memory loading不需要跨narrative去重
- Auxiliary由每个narrative content独立决定
- Context控制更简单

**后续考虑：** Link设计作为实验性内容，通过scope实验后续调试。

#### B3. Narrative与Social Network联动

**问题：** Participant信息在LLM判断中仅使用topic_hint[:50/100]，极其单薄。Social Network有丰富的entity信息（role, description, contact）但未使用。

**共识：** Narrative需与social network解耦。Narrative仅储存actor IDs，具体信息存放于social network。后续迭代优化信息使用效果。

---

## 5. 确认的Bug

| Bug | 严重度 | 描述 | 状态 |
|---|---|---|---|
| **EverMemOS无agent_id隔离** | 高 | 搜索仅用user_id，跨agent的episode污染排名。客户端post-filter是hack（top_k×3） | 必须修复（梁斌确认） |
| **LLM判断处随意截断** | 高 | Summary截断300字符, episodes截断500字符, participant截断50/100字符——都在调用处截断而非数据源内部管理 | 必须修复 |
| **Continuity path不加载EverMemOS** | 中 | 连续性通过时evermemos_memories为空，Agent只看到ChatModule DB最近40条，无语义episode | 对齐"context来源一致"原则 |
| **Job trigger不加载EverMemOS** | 中 | forced_narrative完全跳过EverMemOS，与continuity path同样的gap | 同上 |

---

## 6. 确立的原则

### P1: Context来源一致
Job trigger, continuous, non-continuous应使用相同的分层context：
- Top level: Narrative metadata
- 中间层1: Cluster (optional, from EverMemOS)
- 中间层2: Episode (from EverMemOS)
- 底层: Short-term memory + detailed events (from ChatModule)

### P2: 长度管理交到内部，不在调用处截断
每个数据源在内部提供有界输出。Narrative提供自行compacted的summary；Episode提供有界的summaries。不在LLM调用处截断。

### P3: Narrative互相independent
无跨narrative去重需求。Auxiliary由各narrative自身content决定。无link传递式加载。

### P4: Narrative需要内部限制
Topic scope有下限（大于cluster）。Summary有大小上限（自行compacting）。每个narrative提供有界的context贡献。

### P5: EverMemOS作为可选插件
系统必须在没有EverMemOS时正常运行。长期目标：将独特能力迁移到核心模块，弱化依赖。

---

## 7. 行动项

### 立即修复 (Bug)

| 项目 | 负责人 | 描述 |
|---|---|---|
| EverMemOS agent_id隔离 | Hongyi + EverMemOS团队 | 添加agent_id为一等可过滤字段（EverMemOS约25文件）。已在ACTION_PLAN中完成scope |
| 移除LLM调用处截断 | Hongyi | 移除retrieval.py中300/500/50/100字符截断。数据源应自行限制。特性分支已部分完成 |
| 对齐Continuity path context | Hongyi + Bin | Continuity path也加载EverMemOS episodes（传入query_text到_search）。Job trigger同理 |

### 短期（原则对齐）

| 项目 | 负责人 | 描述 |
|---|---|---|
| Narrative summary compaction | Hongyi + Bin | 在narrative updater中添加compaction机制。超过阈值时LLM压缩。移除description字段，仅保留current_summary |
| 后台整理机制 | Bin + Xiong | 白天增量更新，夜间全局整理：narrative合并拆解、动态更新memory。结合agent可演化机制 |
| Narrative centroid表 | Hongyi | 新增narrative_centroids表，增量event embedding平均。额外的ranking signal。特性分支已实现 |
| Context预算分配 | Hongyi | 定义每个context部分的token预算（narrative summary, episodes, short-term memory）。用结构化限制替代随意截断 |

### 中期（架构）

| 项目 | 负责人 | 描述 |
|---|---|---|
| EverMemOS依赖降低 | Hongyi + Bin + Xiong | 拉平信息层级。将episode摘要+RRF检索迁移到核心模块。弱化EverMemOS直到可无痛移除 |
| Narrative检索逻辑重设计 | Team | 决定top-down / bottom-up / mixed。实现narrative centroid + EverMemOS双维混合检索 |
| Narrative scope实验 | Team | 通过LoCoMo benchmark重跑测试不同scope定义。衡量延迟创建、更大scope等效果 |
| 检索评分优化 | Team | 参考认知科学，从纯向量相似度演化为多信号评分：semantic × recency × importance × frequency |

### 效果评估

| 项目 | 负责人 | 描述 |
|---|---|---|
| 日志整理文档 | Hongyi | 收集足够详细的日志用于narrative机制分析，后续与Bin共同分析问题根源 |
| LoCoMo重跑 | Xiangchao + Hongyi | Bug修复后重跑LoCoMo评估真实上限（当前分数主要反映pipeline failure而非agent能力） |

---

## 8. Narrative动态更新与整理机制（提议）

基于会议讨论（梁斌提议）：

| 阶段 | 时机 | 操作 |
|---|---|---|
| **白天交互期** | 每轮对话 | 增量更新narrative，仅查看新增量信息，实时处理交互流量 |
| **夜间整理期** | 离线/低频时段 | 全局信息整理，narrative合并与拆解，动态更新memory，实化call code和stream机制 |
| **长期演化** | 定期 | 定期优化narrative定义，调整合并拆解频率，结合实际迭代pipeline |

核心：通过分时段处理实现搜索效率与深度优化的平衡，提升agent记忆管理的灵活性。

认知科学支撑：对应睡眠巩固研究（Diekelmann & Born, 2010）——巩固不仅是复制记忆，而是**转化**。慢波睡眠期间海马体回放将信息转移到新皮层，同时提取跨episode规律。Schema一致的记忆巩固更快，Schema违反的记忆保留更详细。

---

## 9. 相关文档

| 文档 | 位置 |
|---|---|
| Narrative Review (原始) | 2026/03/30 Narrative review |
| 会议纪要 | 智能纪要 2026年4月1日 |
| 详细函数追踪 | drafts/narrative_pipeline/NARRATIVE_FUNCTION_TRACE.md |
| 数据生命周期 | drafts/narrative_pipeline/NARRATIVE_DATA_LIFECYCLE.md |
| 全局分析 | drafts/narrative_pipeline/NARRATIVE_BIG_PICTURE.md |
| 技术行动计划 | drafts/narrative_pipeline/NARRATIVE_ACTION_PLAN.md |
| Runtime Pipeline | drafts/runtime_pipeline/RUNTIME_PIPELINE.md |
| 认知科学调研 | compass_artifact (cognitive foundations for AI agent memory) |
