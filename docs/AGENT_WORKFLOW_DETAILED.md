# NexusAgent 完整工作流程详解

> 本文档从用户输入一条消息开始，逐步展开 Agent 内部的每一个处理环节，  
> 区分「首次交互」和「第 N 次交互」的差异，并在关键节点附带注释解释复杂概念。

---

## 0. 全局流程总览

```
用户输入 Query
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 0   初始化                                                 │
│  加载 Agent 配置 → 创建 Event → 获取/创建 Session → 加载 Awareness │
├─────────────────────────────────────────────────────────────────┤
│  Step 1   选择 Narrative（话题故事线）                              │
│  连续性检测 → 向量检索 → 双层阈值 → LLM 裁判 → 返回 Top-K          │
├─────────────────────────────────────────────────────────────────┤
│  Step 1.5 初始化 Markdown 历史                                    │
│  读取 Narrative 对应的历史对话记录文件                               │
├─────────────────────────────────────────────────────────────────┤
│  Step 2   加载模块 + 决定执行路径                                   │
│  LLM 智能决策加载哪些 Module Instance → 启动 MCP Server            │
├─────────────────────────────────────────────────────────────────┤
│  Step 2.5 同步 Instance 变更                                      │
│  更新 Markdown → 同步 Instance 到数据库                            │
├─────────────────────────────────────────────────────────────────┤
│  Step 3   执行路径（核心推理）                                      │
│  ┌─ AGENT_LOOP (99%)：Data Gathering → 合并上下文 → LLM 推理      │
│  └─ DIRECT_TRIGGER (1%)：直接调用 MCP Tool，跳过 LLM              │
├─────────────────────────────────────────────────────────────────┤
│  Step 4   持久化结果                                              │
│  记录 Trajectory → 更新 Event → 更新 Narrative → 更新 Session      │
├─────────────────────────────────────────────────────────────────┤
│  Step 5   执行 Hooks（后处理）                                     │
│  各模块的 hook_after_event_execution()                            │
│  如：社交网络更新实体、记忆模块写入 EverMemOS                        │
├─────────────────────────────────────────────────────────────────┤
│  Step 6   处理 Hook 回调                                          │
│  检查是否有依赖链中的下游 Instance 需要触发                          │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
用户收到 Agent 回复（流式输出）
```

---

## 1. Step 0：初始化

### 流程

```
Step 0 开始
    │
    ├─ 0.1 从数据库加载 Agent 配置
    │      → agent_name, agent_type, agent_description, agent_metadata
    │
    ├─ 0.2 初始化 ModuleService
    │      → 准备模块加载器，后续 Step 2 会用它来加载模块
    │
    ├─ 0.3 创建 Event 记录
    │      → Event 是"从触发到最终输出"的完整过程记录
    │      → 生成唯一 event_id，记录 trigger_type=CHAT, input_content
    │      → 注：Event 是 Narrative 增长的基本单位
    │
    ├─ 0.4 获取/创建 Session
    │      │
    │      ├─ 【首次交互】数据库中没有该 user+agent 的 Session
    │      │   → 创建新 Session（session_id=sess_xxxxxxxx）
    │      │   → query_count=0, last_query=""
    │      │
    │      └─ 【第N次交互】数据库中已有 Session
    │          → 加载已有 Session
    │          → 包含 last_query, last_query_time, current_narrative_id
    │          → 这些信息将用于 Step 1 的连续性检测
    │
    └─ 0.5 加载 Agent Awareness（自我认知）
           → 从 instance_awareness 表读取
           → Awareness 包含：角色定义、目标、行为准则、关键信息
           → 例如："你是一个知识渊博的智者" 或 "你是 Arena 竞赛选手 Loki"
           → 将用于 Step 1 的连续性检测和 Step 3 的 System Prompt
```

### 注解

> **Event 是什么？**  
> Event 代表一次完整的"触发→推理→输出"过程。每次用户发消息、任务触发、API 调用，都会创建一个 Event。  
> Event 包含：触发类型、环境上下文、执行日志、最终输出。  
> 一个 Narrative（话题故事线）由多个 Event 组成。

> **Session 是什么？**  
> Session 追踪一个用户和一个 Agent 之间的**连续会话状态**。  
> 核心作用：记住"上一轮聊了什么"，用于判断"这一轮是否还在同一个话题"。  
> Session 有超时机制——长时间不活动后会过期，下次创建新 Session。

> **Awareness 是什么？**  
> 类似于 Agent 的"人设卡片"。通过自然语言配置，决定 Agent 的角色、性格、行为方式。  
> 用户可以通过聊天随时修改（如"从现在起你是一个卖书的Agent"）。

---

## 2. Step 1：选择 Narrative（最复杂的步骤）

### 首次交互 vs 第N次交互的分支

```
Step 1 开始
    │
    ├─ 检查是否有强制 Narrative（forced_narrative_id）
    │  └─ 有 → 直接加载该 Narrative，跳过检索（用于 Job 任务触发）
    │
    └─ 无强制 → 进入正常选择流程
         │
         │  ┌────────────────────────────────┐
         ├──│  Session 中有 last_query 吗？    │
         │  └──────────┬─────────────────────┘
         │             │
         │      ┌──────┴──────┐
         │      │             │
         │   【首次交互】    【第N次交互】
         │   last_query=""   last_query="上次的问题"
         │      │             │
         │      │             ▼
         │      │     ┌──────────────────────────────────┐
         │      │     │  阶段 A：连续性检测                  │
         │      │     │  (ContinuityDetector)              │
         │      │     │                                    │
         │      │     │  输入给 LLM：                       │
         │      │     │  - 上一轮的问题 + Agent 回答         │
         │      │     │  - 当前 Narrative 的名称/摘要/关键词  │
         │      │     │  - Agent 的 Awareness               │
         │      │     │  - 当前的问题                        │
         │      │     │  - 时间间隔                          │
         │      │     │                                    │
         │      │     │  LLM 判断：                         │
         │      │     │  当前问题是否属于当前 Narrative？      │
         │      │     │                                    │
         │      │     │  ⚠ 注意：对话连续 ≠ 同一 Narrative   │
         │      │     │  例：连续对话中用户突然换了话题         │
         │      │     │                                    │
         │      │     │  输出：{is_continuous, confidence,   │
         │      │     │         reason}                     │
         │      │     └──────────┬───────────────────────────┘
         │      │               │
         │      │        ┌──────┴──────┐
         │      │        │             │
         │      │     属于当前       不属于当前
         │      │     Narrative     Narrative
         │      │        │             │
         │      │        ▼             │
         │      │   沿用当前           │
         │      │   Narrative          │
         │      │   作为主话题          │
         │      │   (但仍检索           │
         │      │    辅助 Top-K)        │
         │      │        │             │
         │      └────────┤             │
         │               │             │
         │               ▼             ▼
         │        ┌─────────────────────────────────────────────┐
         │        │  阶段 B：向量检索 + 匹配决策                    │
         │        │                                              │
         │        │  B.1 确保默认 Narrative 存在                   │
         │        │      首次交互时会创建 8 个预设话题：              │
         │        │      如"日常聊天"、"问候"、"任务管理"等           │
         │        │                                              │
         │        │  B.2 查询 PARTICIPANT Narrative                │
         │        │      检查用户是否作为"参与者"出现在                │
         │        │      其他用户创建的话题中                        │
         │        │      （如销售场景中的目标客户）                    │
         │        │                                              │
         │        │  B.3 生成 Query Embedding                     │
         │        │      调用 OpenAI Embedding API                │
         │        │      "星辰计划预算定了吗？"                      │
         │        │       → [0.12, -0.45, 0.78, ...]  (768维)     │
         │        │                                              │
         │        │  B.4 向量搜索                                  │
         │        │      ┌──────────────────────────────────┐     │
         │        │      │ 两种模式（取决于配置）：              │     │
         │        │      │                                  │     │
         │        │      │ 模式1: EverMemOS（高级）           │     │
         │        │      │   调用 EverMemOS HTTP API         │     │
         │        │      │   使用 RRF 混合检索：              │     │
         │        │      │   BM25(关键词) + Vector(语义)     │     │
         │        │      │   → 返回候选 + episode_summaries  │     │
         │        │      │   → 如果返回空则 fallback 到模式2   │     │
         │        │      │                                  │     │
         │        │      │ 模式2: 本地向量检索（默认/回退）     │     │
         │        │      │   在数据库中的所有 Narrative        │     │
         │        │      │   的 routing_embedding 中          │     │
         │        │      │   计算余弦相似度                    │     │
         │        │      │   按相似度降序排列                  │     │
         │        │      └──────────────────────────────────┘     │
         │        │                                              │
         │        │  B.5 Event 增强评分                            │
         │        │      对每个候选 Narrative：                     │
         │        │      取其最近 N 个 Event 的用户输入              │
         │        │      生成 Embedding → 计算平均向量               │
         │        │      加权融合：                                 │
         │        │      最终分 = 话题分×(1-w) + Event分×w          │
         │        │      → 让"最近活跃"的话题获得加分                 │
         │        │                                              │
         │        │  B.6 双层阈值判断                               │
         │        │                                              │
         │        │      最高分 ≥ 高阈值 (0.8)                      │
         │        │        且无 PARTICIPANT Narrative               │
         │        │        → 高置信度，直接返回 Top-K               │
         │        │                                              │
         │        │      否则 → LLM 统一裁判                       │
         │        │        输入给 LLM：                            │
         │        │        - 搜索结果候选（名称+描述+相似度分数）      │
         │        │        - 默认 Narrative 候选（如日常聊天）        │
         │        │        - PARTICIPANT 候选                      │
         │        │        - 用户的查询                             │
         │        │                                              │
         │        │        LLM 输出：                              │
         │        │        {matched_category, matched_index,       │
         │        │         reason}                                │
         │        │                                              │
         │        │        ┌─ 匹配 PARTICIPANT → 返回该 Narrative  │
         │        │        ├─ 匹配默认类型    → 返回该默认 Narrative │
         │        │        ├─ 匹配搜索结果    → 返回 Top-K 列表     │
         │        │        └─ 无匹配         → 创建新 Narrative    │
         │        │                                              │
         │        └──────────────────────────────────────────────┘
         │
         ▼
    输出：ctx.narrative_list = [主Narrative, 辅助1, 辅助2, ...]
         │
         │  附加操作：
         │  为每个选中的 Narrative 确保当前用户有独立的 ChatModule 实例
         │  （支持多用户在同一话题下各自独立的聊天记录）
         │
         ▼
    更新并持久化 Session：
    session.current_narrative_id = 主 Narrative 的 ID
    session.last_query = 当前用户输入
```

### 注解

> **为什么返回多个 Narrative（Top-K）？**  
> 主 Narrative 是"这轮对话归属的话题"，辅助 Narrative 提供"跨话题的相关上下文"。  
> 例如用户问"星辰计划的人够不够？预算还剩多少？"同时涉及"人员"和"预算"两个话题。

> **创建新 Narrative 时发生了什么？**  
> 1. 从用户输入提取关键词（topic_keywords）  
> 2. 截取输入作为话题提示（topic_hint）  
> 3. 用输入的 Embedding 作为路由向量（routing_embedding）  
> 4. 存入数据库 + 加入向量索引  
> 后续相似话题的查询就能检索到这个 Narrative。

> **连续性检测的成本？**  
> 每次都调用一次轻量 LLM（如 GPT-4o-mini）做判断，通常耗时 < 1秒，费用很低。  
> 但它避免了每次都做完整向量检索，在连续对话场景下节省了大量计算。

---

## 3. Step 1.5：初始化 Markdown 历史

```
Step 1.5
    │
    ├─ 根据 narrative_id 构建文件路径
    │  例：./data/narratives/agent_xxx/narr_yyy.md
    │
    ├─ 【首次交互（新 Narrative）】
    │   → 文件不存在 → 创建空文件
    │   → markdown_history = ""
    │
    └─ 【第N次交互（已有 Narrative）】
        → 读取文件内容
        → 解析出历史对话记录和 Instance 信息
        → markdown_history = "之前的对话和状态信息"
        → 这些内容将作为 LLM 的上下文输入（让 Agent 记住之前聊过什么）
```

### 注解

> **为什么用 Markdown 文件？**  
> 这是一种轻量的"短期记忆"机制。Markdown 文件记录了一个 Narrative 内最近的对话，  
> 直接作为 LLM 的上下文窗口输入，让 Agent 在同一个话题内保持连贯。  
> 与 Narrative 的向量检索（长期记忆）互补。

---

## 4. Step 2：加载模块 + 决定执行路径

```
Step 2 开始
    │
    ▼
┌──────────────────────────────────────────────────────────────┐
│  ModuleService.load_modules()                                 │
│                                                               │
│  输入：                                                       │
│  - narrative_list（选中的 Narrative 列表）                      │
│  - input_content（用户输入）                                   │
│  - markdown_history（历史对话）                                │
│  - awareness（Agent 人设）                                    │
│  - working_source（触发来源：chat/job/callback）               │
│                                                               │
│  ┌──────────────────────────────────────────────────────┐     │
│  │  LLM 智能决策（Instance Decision）                     │     │
│  │                                                      │     │
│  │  系统中可用的模块：                                     │     │
│  │  ┌──────────────────┬───────────────────────────┐    │     │
│  │  │ 模块名             │ 功能                      │    │     │
│  │  ├──────────────────┼───────────────────────────┤    │     │
│  │  │ ChatModule        │ 聊天对话                   │    │     │
│  │  │ AwarenessModule   │ 自我认知管理               │    │     │
│  │  │ BasicInfoModule   │ 基础信息（时间、天气等）      │    │     │
│  │  │ SocialNetworkModule│ 社交网络/实体管理          │    │     │
│  │  │ JobModule         │ 任务调度                   │    │     │
│  │  │ GeminiRAGModule   │ 文档检索                   │    │     │
│  │  │ MemoryModule      │ 长期记忆管理               │    │     │
│  │  └──────────────────┴───────────────────────────┘    │     │
│  │                                                      │     │
│  │  LLM 根据用户输入和上下文判断：                         │     │
│  │  - 需要激活哪些 Module Instance                       │     │
│  │  - 需要新建哪些 Instance                              │     │
│  │  - 需要关闭哪些 Instance                              │     │
│  │  - 执行路径：AGENT_LOOP 还是 DIRECT_TRIGGER           │     │
│  │                                                      │     │
│  │  输出示例：                                           │     │
│  │  {                                                   │     │
│  │    "active": ["chat_inst_01", "rag_inst_01"],        │     │
│  │    "new": [{"module": "JobModule", "reason": "..."}],│     │
│  │    "execution_type": "agent_loop",                   │     │
│  │    "reasoning": "用户询问文档内容，需要RAG模块"         │     │
│  │  }                                                   │     │
│  └──────────────────────────────────────────────────────┘     │
│                                                               │
│  基于决策结果：                                                │
│  1. 实例化每个需要的 Module 对象                               │
│  2. 为需要 MCP 的模块启动 MCP Server                          │
│     - GeminiRAGModule → 端口 7805                            │
│     - JobModule → 端口 7803                                  │
│     - 其他模块各自的端口...                                    │
│  3. 注册 MCP Tools（如 rag_query, rag_upload_file 等）        │
│                                                               │
│  输出：                                                       │
│  - execution_type: "agent_loop" 或 "direct_trigger"          │
│  - active_instances: 激活的 Module Instance 列表               │
│  - 每个 Instance 上绑定了对应的 Module 对象                     │
│  - relationship_graph: Instance 间的依赖关系图（Mermaid 格式）  │
└──────────────────────────────────────────────────────────────┘
```

### 注解

> **AGENT_LOOP vs DIRECT_TRIGGER？**  
> - **AGENT_LOOP**（99% 的情况）：需要 LLM 推理。用户问问题、聊天、执行复杂任务。  
> - **DIRECT_TRIGGER**（1%）：不需要 LLM，直接调用某个 MCP Tool。  
>   例如系统内部的 API 触发，直接调用特定工具即可。

> **为什么用 LLM 来决定加载哪些模块？**  
> 不同的用户输入需要不同的模块组合。例如：  
> - "你好" → 只需 ChatModule  
> - "根据文档回答问题" → 需要 ChatModule + GeminiRAGModule  
> - "每5分钟提醒我" → 需要 ChatModule + JobModule  
> - "记住这个人叫 Alice" → 需要 ChatModule + SocialNetworkModule  
> LLM 能理解用户意图，动态选择最合适的模块组合。

---

## 5. Step 2.5：同步 Instance 变更

```
Step 2.5
    │
    ├─ 2.5.1 更新 Markdown 文件
    │        写入当前激活的 Instance 信息和关系图
    │
    ├─ 2.5.2 同步 Instance 到数据库
    │        ├─ 新增的 Instance → 建立与 Narrative 的关联
    │        ├─ 移除的 Instance → 解除关联
    │        └─ 更新的 Instance → 更新状态
    │
    └─ 2.5.3 处理 Job 创建
           如果 Step 2 决策中包含新建 JobModule Instance
           → 创建对应的 Job 记录到数据库
           → 记录 created_job_ids 传递给后续步骤
```

---

## 6. Step 3：执行路径（核心推理步骤）

### 路径 A：AGENT_LOOP（99% 的情况）

```
Step 3 (AGENT_LOOP) 开始
    │
    ├─ 3.1 初始化 ContextRuntime
    │
    ├─ 3.2 运行 ContextRuntime（构建完整上下文）
    │      │
    │      │  这一步将所有信息合并为 LLM 可消费的格式：
    │      │
    │      │  ┌──────────────────────────────────────────────┐
    │      │  │  Data Gathering Phase                         │
    │      │  │  遍历所有激活的 Module：                        │
    │      │  │                                               │
    │      │  │  ChatModule.hook_data_gathering()              │
    │      │  │    → 加载该用户在该 Narrative 下的聊天记录       │
    │      │  │                                               │
    │      │  │  AwarenessModule.hook_data_gathering()         │
    │      │  │    → 加载 Agent 的 Awareness（人设）            │
    │      │  │                                               │
    │      │  │  GeminiRAGModule.hook_data_gathering()         │
    │      │  │    → 从数据库加载知识库关键词                    │
    │      │  │    → 替换 instructions 中的 {rag_keywords}      │
    │      │  │    → Agent 就知道知识库里有什么内容               │
    │      │  │                                               │
    │      │  │  SocialNetworkModule.hook_data_gathering()     │
    │      │  │    → 加载相关的实体和关系信息                    │
    │      │  │                                               │
    │      │  │  JobModule.hook_data_gathering()               │
    │      │  │    → 加载当前活跃的任务列表                      │
    │      │  │                                               │
    │      │  │  MemoryModule.hook_data_gathering()            │
    │      │  │    → 从 EverMemOS 加载长期记忆                  │
    │      │  │                                               │
    │      │  │  每个模块返回：                                 │
    │      │  │  - instructions（指令：告诉 Agent 该模块能做什么）│
    │      │  │  - ctx_data（数据：该模块收集到的上下文信息）     │
    │      │  └──────────────────────────────────────────────┘
    │      │
    │      │  ┌──────────────────────────────────────────────┐
    │      │  │  Context Merging Phase                        │
    │      │  │                                               │
    │      │  │  合并所有模块的 instructions → System Prompt    │
    │      │  │  合并所有模块的 ctx_data → 上下文信息            │
    │      │  │  收集所有模块的 MCP Server URL                 │
    │      │  │  加载 markdown_history（短期对话记录）           │
    │      │  │  加载 EverMemOS memories（长期情景记忆）         │
    │      │  │                                               │
    │      │  │  最终构建出：                                   │
    │      │  │  - messages: 消息列表（System + History + User）│
    │      │  │  - mcp_urls: MCP 工具服务地址列表               │
    │      │  └──────────────────────────────────────────────┘
    │      │
    │      └─ 输出：context 对象（messages + mcp_urls）
    │
    ├─ 3.3 提取 messages 和 MCP URLs
    │      │
    │      │  最终的消息结构（发送给 LLM）：
    │      │
    │      │  ┌─────────────────────────────────────────────────────┐
    │      │  │ [System Message]                                     │
    │      │  │   你是 Agent "Loki"...（来自 Awareness）              │
    │      │  │                                                     │
    │      │  │   ## RAG Module - Document Search Capability         │
    │      │  │   Keywords: 产品规格, 方案A, 方案B...                  │
    │      │  │   （来自 GeminiRAGModule.instructions）               │
    │      │  │                                                     │
    │      │  │   ## Social Network                                  │
    │      │  │   已知实体：Alice(前端), Bob(后端)...                   │
    │      │  │   （来自 SocialNetworkModule.instructions）            │
    │      │  │                                                     │
    │      │  │   ## Jobs                                            │
    │      │  │   活跃任务：心跳监控(每15分钟)...                       │
    │      │  │   （来自 JobModule.instructions）                      │
    │      │  ├─────────────────────────────────────────────────────┤
    │      │  │ [History Messages]                                   │
    │      │  │   来自 markdown_history + ChatModule 的聊天记录        │
    │      │  │   user: "星辰计划预算是320万"                          │
    │      │  │   assistant: "好的，我已记录..."                       │
    │      │  │   ...                                               │
    │      │  ├─────────────────────────────────────────────────────┤
    │      │  │ [User Message]                                       │
    │      │  │   "方案A和方案B的主要区别是什么？"                      │
    │      │  └─────────────────────────────────────────────────────┘
    │      │
    │      │  可用的 MCP Tools：
    │      │  ┌────────────────────────────────────────────────┐
    │      │  │ gemini_rag_module (http://127.0.0.1:7805/sse)  │
    │      │  │   → rag_query, rag_upload_file, rag_upload_text│
    │      │  │                                                │
    │      │  │ job_module (http://127.0.0.1:7803/sse)         │
    │      │  │   → create_job, update_job, list_jobs          │
    │      │  │                                                │
    │      │  │ ...其他模块的 MCP Tools                         │
    │      │  └────────────────────────────────────────────────┘
    │      │
    │
    ├─ 3.4 运行 Agent Loop（ClaudeAgentSDK）
    │      │
    │      │  使用 Claude Code 作为核心 Agent 运行时
    │      │
    │      │  ┌──────────────────────────────────────────────────────────┐
    │      │  │  Agent Loop（支持多轮 Tool 调用）                          │
    │      │  │                                                          │
    │      │  │  第1轮：                                                  │
    │      │  │    LLM 阅读 System Prompt + History + User Message       │
    │      │  │    LLM 思考："用户问方案A和B的区别，我的RAG关键词里有        │
    │      │  │              '方案A'和'方案B'，应该用 rag_query 检索"      │
    │      │  │    LLM 调用 Tool：rag_query(query="方案A 方案B 区别")      │
    │      │  │                      ↓                                   │
    │      │  │    MCP Server 执行 → Gemini File Search API 检索          │
    │      │  │                      ↓                                   │
    │      │  │    返回文档片段：[{text: "方案A采用...", title: "规格书"}]   │
    │      │  │                                                          │
    │      │  │  第2轮：                                                  │
    │      │  │    LLM 收到 Tool 结果                                     │
    │      │  │    LLM 思考："我已经有了文档内容，可以回答用户了"            │
    │      │  │    LLM 生成最终回答（流式输出 AgentTextDelta）             │
    │      │  │    "根据产品规格书，方案A和方案B的主要区别在于..."           │
    │      │  │                                                          │
    │      │  │  ⚠ Agent Loop 可能进行多轮 Tool 调用：                    │
    │      │  │    检索文档 → 查询社交网络 → 创建任务 → 最终回答            │
    │      │  │    直到 LLM 认为可以给出最终回答为止                        │
    │      │  └──────────────────────────────────────────────────────────┘
    │      │
    │      └─ 流式输出：每个 token 都通过 AgentTextDelta yield 给前端
    │
    └─ 3.5 收集结果
           → final_output: 完整的 Agent 回答文本
           → execution_steps: 执行步骤记录
           → agent_loop_response: 原始响应（包含 Tool 调用记录）
```

### 路径 B：DIRECT_TRIGGER（1% 的情况）

```
Step 3 (DIRECT_TRIGGER) 开始
    │
    ├─ 解析 direct_trigger 配置
    │  - module_class: 目标模块（如 "JobModule"）
    │  - trigger_name: MCP Tool 名称（如 "execute_job"）
    │  - params: 调用参数
    │
    ├─ 找到对应模块的 MCP Server URL
    │
    ├─ 直接调用 MCP Tool（跳过 LLM 推理）
    │
    └─ 返回执行结果
```

### 注解

> **为什么用 Claude Code 作为 Agent 运行时？**  
> Claude Code 提供了原生的 Tool Use 能力和流式输出，  
> 能够在一次对话中进行多轮推理和工具调用（ReAct 模式），  
> 直到 Agent 认为已经收集够了信息，可以给出最终回答。

> **MCP Tool 调用的完整链路？**  
> Agent 决定调用 `rag_query` →  
> 通过 MCP 协议（SSE）发送请求到 `127.0.0.1:7805` →  
> FastMCP Server 接收并路由到 `rag_query()` 函数 →  
> 函数内部调用 `GeminiRAGModule.query_store()` →  
> 调用 Google Gemini File Search API →  
> 返回文档片段 →  
> 通过 MCP 协议返回给 Agent →  
> Agent 继续推理

---

## 7. Step 4：持久化结果

```
Step 4 开始
    │
    ├─ 4.1 记录 Trajectory（执行轨迹文件）
    │      完整记录这次执行的所有步骤，用于调试和审计
    │
    ├─ 4.2 更新 Markdown
    │      将本轮对话追加到 Narrative 对应的 Markdown 文件
    │      下次同一话题时，Step 1.5 会读取这些内容
    │
    ├─ 4.3 更新 Event
    │      │
    │      ├─ event.final_output = Agent 的回答
    │      ├─ event.event_log = 执行日志（每步操作的时间戳和内容）
    │      ├─ event.module_instances = 本次使用的模块实例列表
    │      └─ 保存到数据库
    │
    ├─ 4.4 更新 Narrative
    │      │
    │      │  对每个选中的 Narrative：
    │      │
    │      │  ├─ 主 Narrative（narrative_list[0]）：
    │      │  │   ├─ 将 event_id 添加到 event_ids 列表
    │      │  │   ├─ 追加 dynamic_summary 条目（本轮摘要）
    │      │  │   ├─ events_since_last_embedding_update += 1
    │      │  │   │
    │      │  │   ├─ 【条件触发】每 N 轮 → 异步 LLM 更新：
    │      │  │   │   LLM 重新生成 Narrative 的：
    │      │  │   │   - name（标题）
    │      │  │   │   - current_summary（摘要）
    │      │  │   │   - topic_keywords（关键词）
    │      │  │   │   - dynamic_summary（最后一条的优化版本）
    │      │  │   │   ⚠ 异步执行，不阻塞主流程
    │      │  │   │
    │      │  │   └─ 【条件触发】累积足够 Event → 异步更新 Embedding：
    │      │  │       基于最新的 name + summary 重新生成 routing_embedding
    │      │  │       更新向量索引
    │      │  │       → 下次检索时，这个 Narrative 的匹配会更准确
    │      │  │
    │      │  └─ 辅助 Narrative（narrative_list[1:]）：
    │      │      ├─ 将 event_id 添加到 event_ids 列表
    │      │      └─ 不触发 LLM 更新和 Embedding 更新
    │      │         （只做轻量记录，避免过多开销）
    │      │
    │
    └─ 4.5 更新 Session
           session.last_query = 用户输入
           session.last_response = Agent 回答
           session.last_query_time = 当前时间
           → 持久化到数据库
           → 下一轮 Step 1 的连续性检测会用到这些信息
```

### 注解

> **为什么 Narrative 的 Embedding 要周期性更新？**  
> 随着对话深入，Narrative 的话题可能发生漂移。  
> 例如：一开始聊"项目预算"，后来深入到"预算审批流程"。  
> 定期用最新的摘要重新生成 Embedding，让向量检索更准确。

> **异步更新为什么重要？**  
> LLM 更新 Narrative 元数据和重新生成 Embedding 都需要调用 API，耗时数秒。  
> 如果同步执行，用户每轮对话都要多等几秒。  
> 异步执行让用户立即收到回复，后台默默优化 Narrative 的质量。

---

## 8. Step 5：执行 Hooks（后处理）

```
Step 5 开始
    │
    │  构建 HookAfterExecutionParams：
    │  - execution_ctx: event_id, agent_id, user_id, working_source
    │  - io_data: input_content（用户输入）, final_output（Agent 回答）
    │  - trace: event_log, agent_loop_response（含 Tool 调用记录）
    │  - instance: 当前执行的 Module Instance
    │  - narrative: 当前 Narrative
    │
    ├─ ChatModule.hook_after_event_execution()
    │  → 将本轮对话记录保存到数据库
    │
    ├─ AwarenessModule.hook_after_event_execution()
    │  → 检查 Agent 回答中是否有 Awareness 更新指令
    │  → 如果有，更新 Agent 的 Awareness
    │
    ├─ SocialNetworkModule.hook_after_event_execution()
    │  → 分析对话中提到的人物/实体
    │  → 更新社交网络图谱
    │
    ├─ GeminiRAGModule.hook_after_event_execution()
    │  → （如果有文件上传）更新关键词
    │
    ├─ JobModule.hook_after_event_execution()
    │  → 检查是否有任务状态变更
    │  → 标记已完成/失败的任务
    │  → 返回需要触发的下游 Instance（依赖链）
    │
    └─ MemoryModule.hook_after_event_execution()
       → 将本轮对话写入 EverMemOS（长期记忆）
       → 包括对话内容的情景提取和语义索引
       → 未来跨会话检索时能回忆起这次对话
```

---

## 9. Step 6：处理 Hook 回调

```
Step 6 开始
    │
    ├─ 检查 Step 5 收集的 callback_results
    │
    ├─ 如果有需要触发的下游 Instance：
    │  │
    │  │  场景示例：
    │  │  任务 A 完成 → 任务 B 依赖 A → 自动触发任务 B
    │  │
    │  ├─ 检查依赖是否全部满足
    │  ├─ 如果满足 → 在后台异步启动新的 run() 流程
    │  │   working_source = CALLBACK
    │  │   input_content = "[CALLBACK] Instance xxx activated"
    │  │   → 走完整的 Step 0 ~ Step 6 流程
    │  │   → 异步执行，不阻塞当前请求
    │  │
    │  └─ 更新 Instance 状态到 Narrative
    │
    └─ 清理日志处理器
       → 本次 run() 流程结束
```

---

## 10. 首次交互 vs 第N次交互 对比总结

| 步骤 | 首次交互 | 第N次交互（同话题） | 第N次交互（新话题） |
|:---:|:---|:---|:---|
| **Step 0.4** | 创建新 Session | 加载已有 Session（含 last_query） | 加载已有 Session |
| **Step 1 连续性** | 跳过（无 last_query） | LLM 判断 → **属于当前话题** → 沿用 | LLM 判断 → **不属于** → 进入检索 |
| **Step 1 检索** | 创建默认 Narrative + 可能创建新 Narrative | 跳过（连续性通过） | 向量检索 → 匹配已有或创建新的 |
| **Step 1.5** | Markdown 文件不存在 → 空 | 读取历史对话 → 有上下文 | 新 Narrative → 空（或已有 → 有上下文） |
| **Step 2** | LLM 决策模块（基于用户意图） | LLM 决策（可能复用已有 Instance） | LLM 决策（可能调整模块组合） |
| **Step 3** | Agent 无历史上下文 | Agent 有完整历史上下文 | Agent 有新话题的上下文 |
| **Step 4** | 创建首个 Event，Narrative 初始化 | 追加 Event，可能触发 LLM 更新 | 追加 Event 到新/已有 Narrative |

---

## 11. 关键概念速查表

| 概念 | 定义 | 生命周期 |
|:---|:---|:---|
| **Event** | 一次"触发→推理→输出"的完整过程 | 每次用户消息创建一个，不可变 |
| **Narrative** | 话题故事线的路由索引 | 跨会话持久存在，动态更新元数据 |
| **Session** | 用户-Agent 的连续会话状态 | 超时后过期，用于连续性检测 |
| **Module** | 功能模块（RAG、任务、社交网络等） | 按需加载，每次 run() 可能不同 |
| **Instance** | Module 的运行实例 | 与 Narrative 关联，有状态（active/completed/failed） |
| **Awareness** | Agent 的自我认知/人设 | 持久存在，可通过聊天动态修改 |
| **MCP Server** | Module 暴露工具的通信服务 | 随 Module 启动/停止 |
| **MCP Tool** | Agent 可调用的具体工具函数 | 注册在 MCP Server 上 |
| **Embedding** | 文本的向量表示（768 维浮点数组） | 用于语义相似度计算 |
| **Markdown History** | Narrative 的短期对话记录文件 | 每轮追加，作为 LLM 上下文窗口 |
| **EverMemOS** | 长期情景记忆系统 | 跨会话持久存在，语义检索 |
| **Trajectory** | 执行轨迹文件 | 每次 run() 生成一个，用于调试 |
| **Hook** | 模块的生命周期回调函数 | 在特定阶段自动调用 |
| **ContextRuntime** | 上下文构建器 | 合并所有模块的信息为 LLM 输入 |

---

## 12. 数据流向总图

```
                    用户输入
                       │
                       ▼
               ┌───────────────┐
               │   AgentRuntime │
               │    .run()      │
               └───────┬───────┘
                       │
        ┌──────────────┼──────────────────────────────┐
        ▼              ▼                              ▼
   ┌─────────┐  ┌─────────────┐              ┌──────────────┐
   │ Database │  │  Narrative  │              │   Vector     │
   │  MySQL   │  │  Service    │              │   Store      │
   │          │  │             │              │  (检索索引)   │
   │ - Agent  │  │ - select()  │──embedding──→│              │
   │ - Event  │  │ - update()  │←─ 匹配结果 ──│              │
   │ - Session│  │ - CRUD      │              └──────────────┘
   │ - Module │  └──────┬──────┘
   │   Inst.  │         │
   │ - RAG    │         │
   │   Store  │         ▼
   │ - ...    │  ┌─────────────┐     ┌──────────────────────┐
   └──────────┘  │  Context    │     │    MCP Servers        │
                 │  Runtime    │     │                      │
                 │  (上下文     │     │  RAG:7805 ──→ Gemini │
                 │   合并器)    │     │  Job:7803 ──→ DB     │
                 └──────┬──────┘     │  Social:7804 ──→ DB  │
                        │            │  ...                 │
                        ▼            └──────────┬───────────┘
                 ┌─────────────┐               │
                 │  Claude Code │←── MCP 协议 ──┘
                 │  Agent Loop  │
                 │  (LLM 推理   │
                 │   + Tool Use)│
                 └──────┬──────┘
                        │
                        ▼
                 ┌─────────────┐
                 │ 流式输出回复  │──→ 用户看到 Agent 回答
                 └─────────────┘
                        │
                        ▼
                 ┌─────────────┐     ┌──────────────────┐
                 │  Hooks       │──→  │  EverMemOS       │
                 │  (后处理)    │     │  (长期记忆)       │
                 │              │     │  MongoDB+ES+     │
                 │              │     │  Milvus          │
                 └──────────────┘     └──────────────────┘
```
