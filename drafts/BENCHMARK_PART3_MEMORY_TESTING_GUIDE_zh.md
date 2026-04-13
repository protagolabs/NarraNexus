# NexusMind -- 对话记忆基准测试指南

**第三部分：记忆基准测试（LoCoMo、MemoryAgentBench、LongMemEval 等）**

---

## 1. 核心挑战

标准记忆基准测试（LoCoMo、MemoryAgentBench、LongMemEval）假设一个简单模型：给智能体一段长对话历史，然后提问。但 NexusMind 的记忆架构**根本不同** -- 它被设计为通过 7 步流水线在每轮对话后**增量构建记忆**：

```
标准基准测试假设：
    [300 轮对话] → [加载到智能体] → [提问]

NexusMind 的实际流程：
    第 1 轮 → Step 0-6（钩子触发：摘要、实体提取、写入 EverMemOS）→
    第 2 轮 → Step 0-6（钩子再次触发）→
    ...
    第 300 轮 → Step 0-6 →
    现在才能提问
```

这带来三个具体问题：

| 问题 | 描述 |
|------|------|
| **速度** | 将 300 轮对话通过完整 7 步流水线（Step 1、2、3、4、5 都有 LLM 调用）可能需要数小时 |
| **叙事分裂** | NexusMind 的 ContinuityDetector 可能因为话题转换而将单个 LoCoMo 对话拆分为多个 Narrative |
| **摘要有损** | 每轮触发 `dynamic_summary` 更新和 EverMemOS 情景提取 -- 原始逐字对话可能被压缩/丢失 |

本指南提供**多种注入策略**，按保真度和实用性排序。

---

## 2. 记忆基准测试概览

### 2.1 LoCoMo（长对话记忆）

**来源**：Snap Research / ACL 2024
**论文**：[arXiv:2402.17753](https://arxiv.org/abs/2402.17753) | [GitHub: snap-research/locomo](https://github.com/snap-research/locomo)

| 方面 | 详情 |
|------|------|
| 对话数 | 10（公开版）/ 50（完整版）|
| 每段对话轮次 | ~300 轮平均 |
| 每段对话 Token 数 | ~9,000 平均 |
| 每段对话会话数 | ~19 个平均 |
| 问题类型 | 单跳 (36%)、多跳 (15%)、时序 (21%)、开放域 (4%)、对抗性 (25%) |
| QA 对总数 | 7,512 |
| 评分方式 | Token 级 F1（QA）、ROUGE + FactScore（摘要）|

**数据格式**（`locomo10.json`）：
```json
{
  "sample_id": "locomo_1",
  "conversation": {
    "speaker_a": "Angela",
    "speaker_b": "Marcus",
    "session_1": [
      {"speaker": "Angela", "dia_id": "1_1", "text": "嗨 Marcus！周末怎么样？"},
      {"speaker": "Marcus", "dia_id": "1_2", "text": "太好了！我终于去了市中心那个新画廊。"}
    ],
    "session_1_date_time": "2023-01-15 14:30",
    "session_2": [...],
    "session_2_date_time": "2023-02-03 10:00"
  },
  "qa": [
    {
      "question": "Marcus 周末做了什么？",
      "answer": "Marcus 参观了市中心的一个新画廊。",
      "category": "single-hop",
      "evidence": ["1_2"]
    }
  ]
}
```

### 2.2 MemoryAgentBench

**来源**：HUST / ICLR 2026
**论文**：[arXiv:2507.05257](https://arxiv.org/abs/2507.05257) | [GitHub: HUST-AI-HYZ/MemoryAgentBench](https://github.com/HUST-AI-HYZ/MemoryAgentBench)

| 方面 | 详情 |
|------|------|
| 规模 | 每个序列最多 144 万 Token |
| 输入方式 | **增量分块**（非全量上下文）|
| 能力维度 | 精确检索、测试时学习、长距离理解、冲突解决 |
| 评分方式 | SubEM、Accuracy、ROUGE-F1 |

与 LoCoMo 的关键区别：MemoryAgentBench **逐块喂入文本**并附带"记住这段内容"的指令，这更接近 NexusMind 的逐轮处理架构。

### 2.3 LongMemEval

**来源**：Microsoft / ICLR 2025
**论文**：[arXiv:2410.10813](https://arxiv.org/abs/2410.10813) | [GitHub: xiaowu0162/LongMemEval](https://github.com/xiaowu0162/LongMemEval)

| 方面 | 详情 |
|------|------|
| 问题数 | 500 个精选 |
| 规模变体 | S: ~40 个会话 / ~115K Token；M: ~500 个会话 / ~150 万 Token |
| 记忆能力 | 信息提取、多会话推理、时序推理、知识更新、弃权 |
| 评分方式 | LLM-as-Judge（GPT-4o，与人类专家 >97% 一致）|
| 评估模式 | **在线**（顺序会话摄入）和**离线**（完整历史）|

### 2.4 其他相关基准测试

| 基准测试 | 年份 | 规模 | 核心焦点 |
|----------|------|------|---------|
| **MSC**（多会话聊天）| 2022 | 5 个会话 | 跨会话的人格一致性 |
| **ConvoMem** | 2025 | 75,336 个 QA 对 | 变化的事实、隐含联系；发现 RAG 在超过 ~150 个对话后变得必要 |
| **Mem2ActBench** | 2026 | 不定 | 记忆到行动：智能体能否使用记忆做出正确的工具调用？|

---

## 3. NexusMind 记忆层 vs. 基准测试需求

### 3.1 哪些基准测试任务测试哪些记忆层

```
┌─────────────────────────────────────────────────────────────────────────┐
│               基准测试 → 记忆层 映射                                      │
├───────────────────────┬─────────────────────────────────────────────────┤
│                       │  NexusMind 记忆层                                │
│ 基准测试任务           │  聊天    叙事     EverMemOS  社交    全部        │
│                       │  历史    摘要     (长期记忆)   图谱              │
├───────────────────────┼─────────────────────────────────────────────────┤
│ 单跳召回              │  ✓✓✓     ✓         ✓✓        -       -         │
│ 多跳推理              │  ✓✓      ✓✓        ✓✓        ✓       -         │
│ 时序推理              │  ✓✓      ✓         ✓         -       -         │
│ 对抗性（弃权）         │  -       -         -         -      ✓✓✓       │
│ 实体/人物召回          │  ✓       -         -        ✓✓✓     -         │
│ 知识更新              │  ✓✓      ✓         ✓        ✓✓      -         │
│ 冲突解决              │  ✓✓      -         ✓        ✓✓      -         │
│ 长距离摘要            │  -      ✓✓✓        ✓✓        -       -         │
│ 跨会话推理            │  ✓(ST)   ✓✓        ✓✓✓       -       -         │
└───────────────────────┴─────────────────────────────────────────────────┘
LT = 长期轨道, ST = 短期轨道（跨叙事）
```

### 3.2 记忆层特性

| 层级 | 保真度 | 容量 | 跨叙事 | 最适合 |
|------|--------|------|--------|--------|
| **聊天历史 (LT)** | 逐字消息 | 每轮 ~40 条消息（每条截断 4000 字符）| 否 | 单跳、近期召回 |
| **聊天历史 (ST)** | 截断至 200 字符 | 最近 15 条消息 | 是 | 跨主题感知 |
| **叙事 dynamic_summary** | LLM 压缩 | 每个事件 1 条摘要 | 否 | 长距离理解 |
| **EverMemOS** | 情景级摘要 | 每个叙事最多 5 个情景，1500 字符显示 | 是 | 跨对话语义搜索 |
| **社交图谱** | 实体属性 | 无限实体 | 是（智能体级）| 人物/实体召回 |

### 3.3 有损问题

当 300 轮对话流经 NexusMind 的流水线时，信息被逐步压缩：

```
原始: 300 轮 × ~30 Token/轮 = ~9,000 Token（逐字）
    ↓
聊天历史 (LT): 最近 ~40 轮逐字保留；更早的轮次被丢弃
    ↓
叙事摘要: 每轮 ~1 句话 = ~300 句话（压缩）
    ↓
EverMemOS: 3-5 个情景 × ~300 Token/个 = ~1,500 Token（高度压缩）
    ↓
社交图谱: 仅实体属性（姓名、专业领域、交互次数）
```

**含义**：对于关于 300 轮对话中第 5 轮的单跳问题，逐字聊天历史已经丢弃了它。答案必须通过叙事摘要或 EverMemOS 存活下来 -- 两者都是有损的。

---

## 4. 注入策略

### 策略 1：完整流水线回放（最高保真度，最慢）

**方法**：通过 `AgentRuntime.run()` 回放每轮对话，让所有钩子自然触发。

**适用场景**：当你想测试 NexusMind 的记忆**在生产环境中的实际表现** -- 包括所有压缩、摘要和实体提取。

**问题**：
- **极慢**：每轮 ~30-60 秒 × 300 轮 = 每段对话 ~3-5 小时
- **智能体生成自己的回复**：智能体的回复会替换 `speaker_b` 的原始回复
- **叙事分裂**：LoCoMo 会话中的话题转换可能导致 NexusMind 创建多个 Narrative

**缓解措施**：
- 使用 `forced_narrative_id` 强制所有轮次进入同一个 Narrative
- 完成后用基准测试的原始回复覆盖 `final_output`

**最适合**：小规模测试（1-3 段对话），生产保真度评估。

---

### 策略 2：选择性流水线回放（中等保真度，中等速度）

**方法**：仅将部分轮次通过完整流水线运行，其余直接注入数据库。

```
对每段 LoCoMo 对话：
    1. 数据库直接注入所有轮次到聊天历史（策略 3）
    2. 每隔 N 轮（如每 10 轮）通过完整流水线运行
       → 触发叙事摘要更新和 EverMemOS 写入
    3. 最后 5 轮通过完整流水线运行
       → 确保近期上下文是新鲜的
```

**最适合**：中等规模测试（5-20 段对话），合理的保真度。

---

### 策略 3：直接数据库注入（摘要/长期记忆保真度最低，最快）

**方法**：完全绕过流水线，直接写入各记忆层的数据库表。

这是**大规模基准测试的推荐方法**，因为它快速、可复现，且让你完全控制每个记忆层的内容。

#### 3a. 注入聊天历史

将完整对话写入 `instance_json_format_memory_chat` 表：

```python
async def inject_chat_history(db_client, agent_id, user_id, conversation, instance_id, narrative_id):
    """将 LoCoMo 对话注入 ChatModule 的实例记忆。"""
    messages = []
    event_ids = []

    session_idx = 1
    while f"session_{session_idx}" in conversation:
        session_key = f"session_{session_idx}"
        date_key = f"session_{session_idx}_date_time"
        session_time = conversation.get(date_key, datetime.utcnow().isoformat())

        for turn in conversation[session_key]:
            dia_id = turn["dia_id"]
            event_id = f"evt_locomo_{dia_id}"
            event_ids.append(event_id)

            role = "user" if turn["speaker"] == conversation["speaker_a"] else "assistant"
            messages.append({
                "role": role,
                "content": turn["text"],
                "meta_data": {
                    "event_id": event_id,
                    "timestamp": session_time,
                    "instance_id": instance_id,
                    "working_source": "chat",
                    "memory_type": "long_term",
                    "session_idx": session_idx,
                    "dia_id": dia_id
                }
            })
        session_idx += 1

    memory = {
        "messages": messages,
        "last_event_id": event_ids[-1] if event_ids else "",
        "updated_at": datetime.utcnow().isoformat()
    }

    await db_client.execute(
        """INSERT INTO instance_json_format_memory_chat (instance_id, memory)
           VALUES (%s, %s) AS nv
           ON DUPLICATE KEY UPDATE memory = nv.memory""",
        params=(instance_id, json.dumps(memory, ensure_ascii=False))
    )
    return event_ids
```

#### 3b. 注入叙事

利用 LoCoMo 提供的 `session_summary` 作为 `dynamic_summary`：

```python
async def inject_narrative(db_client, agent_id, narrative_id, conv_data, event_ids):
    """创建带有预填充摘要的 Narrative。"""
    dynamic_summary = []
    if "session_summary" in conv_data:
        for s_key in sorted(conv_data["session_summary"].keys()):
            idx = s_key.replace("session_", "").replace("_summary", "")
            dynamic_summary.append({
                "event_id": f"evt_locomo_session_{idx}",
                "summary": conv_data["session_summary"][s_key],
                "timestamp": conv_data["conversation"].get(f"session_{idx}_date_time", "")
            })

    # ... 写入 narratives 表
```

#### 3c. 注入 EverMemOS

将所有消息写入 EverMemOS HTTP API，让其异步处理情景提取：

```python
# EverMemOS HTTP API: POST /api/v1/memories
# 对每条消息：
{
    "message_id": "evt_locomo_1_1",
    "create_time": "2023-01-15T14:30:00Z",
    "sender": "Angela",
    "role": "user",
    "type": "text",
    "content": "嗨 Marcus！周末怎么样？",
    "group_id": "nar_locomo_001",
    "group_name": "LoCoMo: Angela & Marcus",
    "scene": "assistant"
}
```

#### 3d. 注入社交图谱

从 LoCoMo 的 `observation` 数据中预提取实体：

```python
# 直接写入 instance_social_entities 表
await db_client.execute(
    """INSERT INTO instance_social_entities
       (instance_id, entity_id, entity_type, entity_name,
        entity_description, interaction_count)
       VALUES (%s, %s, %s, %s, %s, %s)
       ON DUPLICATE KEY UPDATE interaction_count = VALUES(interaction_count)""",
    params=(instance_id, speaker_name, "user", speaker_name,
            "LoCoMo 对话中的说话者", turn_count)
)
```

**策略 3 的局限性**：
- 叙事 `dynamic_summary` 必须手动构建（可使用 LoCoMo 提供的 `session_summary`）
- EverMemOS 情景提取不会自动运行（需要写入原始消息后等待异步处理）
- 不会生成 Narrative 的路由 embedding（语义叙事搜索不可用，除非手动计算并插入 embedding）
- 社交实体描述是最小化的（没有 LLM 生成的沟通画像）

**最适合**：大规模基准测试、可复现性、速度。

---

### 策略 4：混合方案 -- 数据库注入 + EverMemOS 回放（推荐）

**方法**：结合数据库注入的速度和 EverMemOS 自然处理的保真度。

```
步骤 1: 数据库注入聊天历史、事件和叙事（策略 3a-3c）
        → 为 ChatModule 提供逐字召回
        → 每段对话约几秒钟

步骤 2: 通过 HTTP API 将所有消息写入 EverMemOS（策略 3d）
        → EverMemOS 异步处理：
           边界检测 → 情景提取 → embedding 生成
        → 提供长期语义记忆
        → 写入约几秒钟，处理约几分钟

步骤 3: （可选）将 5-10 个关键轮次通过完整流水线运行
        → 触发社交实体提取
        → 生成叙事路由 embedding
        → 每段对话约几分钟

步骤 4: 等待 EverMemOS 处理完成
        → 通过 EverMemOS API 检查情景可用性

步骤 5: 现在通过正常 AgentRuntime.run() 提问基准测试问题
```

**这是推荐方法**，因为：
- 聊天历史是逐字的（近期轮次无损失）
- EverMemOS 有完整对话数据（情景提取自然运行）
- 足够快，可处理所有 10-50 段 LoCoMo 对话
- 叙事摘要可使用 LoCoMo 提供的 `session_summary` 数据

---

## 5. 实际操作分步指南

### 5.1 环境准备

```bash
# 1. 确保所有服务运行中
bash run.sh  # 选择 "Run"

# 2. 验证 EverMemOS 运行中（如测试长期记忆）
docker ps | grep evermemos  # 应显示 MongoDB、Elasticsearch、Milvus、Redis

# 3. 创建基准测试智能体
# 通过前端: http://localhost:5173 → 创建智能体
# 记录 agent_id（如 "agent_benchmark_001"）

# 4. 下载 LoCoMo 数据集
git clone https://github.com/snap-research/locomo.git /tmp/locomo
# 关键文件: /tmp/locomo/data/locomo10.json
```

### 5.2 运行注入脚本

```bash
cd /path/to/NexusAgent

python scripts/inject_locomo.py \
    --locomo-file /tmp/locomo/data/locomo10.json \
    --agent-id agent_benchmark_001 \
    --user-id user_benchmark \
    --strategy hybrid
```

### 5.3 提问基准测试问题

注入完成后，通过正常对话提问，使用 `forced_narrative_id` 确保问题路由到正确的叙事：

```python
async for msg in runtime.run(
    agent_id=agent_id,
    user_id=user_id,
    input_content=question,
    working_source=WorkingSource.CHAT,
    forced_narrative_id=narrative_id  # 关键：强制路由到正确叙事
):
    if hasattr(msg, "content"):
        response_text += msg.content
```

### 5.4 按问题类别评估

| 类别 | 主要记忆层 | 关注要点 |
|------|----------|---------|
| **单跳** | 聊天历史 (LT) | 智能体能否找到某一轮的特定事实？如果该轮较旧，检查 EverMemOS 或叙事摘要是否保留了它 |
| **多跳** | 聊天历史 + 叙事摘要 | 智能体必须综合多轮信息 -- 检查两个轮次是否都可访问 |
| **时序** | 聊天历史（时间戳）+ 叙事摘要 | 智能体必须推理时间顺序 -- 检查 `meta_data.timestamp` 和 `session_idx` 是否被使用 |
| **开放域** | LLM 自身知识 | 非记忆测试；智能体应使用世界知识 |
| **对抗性** | 所有层（弃权）| 智能体应回答"我不知道" -- 检查是否通过混淆记忆产生幻觉 |

---

## 6. MemoryAgentBench 专项指南

MemoryAgentBench 的**增量分块投递**实际上比 LoCoMo 更适合 NexusMind，因为 NexusMind 本身就是逐轮处理对话的。

### 方法：直接逐轮摄入

```python
async def run_memory_agent_bench(agent_id, user_id, chunks, questions):
    """
    MemoryAgentBench 逐块喂入，然后提问。
    这直接映射到 NexusMind 的自然流程。
    """
    runtime = AgentRuntime()

    # 阶段 1: 喂入分块（记忆阶段）
    for i, chunk in enumerate(chunks):
        instruction = f"请记住以下内容：\n\n{chunk}"
        async for msg in runtime.run(
            agent_id=agent_id,
            user_id=user_id,
            input_content=instruction,
            working_source=WorkingSource.CHAT
        ):
            pass  # 记忆阶段丢弃回复

    # 阶段 2: 提问
    results = []
    for qa in questions:
        response = ""
        async for msg in runtime.run(
            agent_id=agent_id,
            user_id=user_id,
            input_content=qa["question"],
            working_source=WorkingSource.CHAT
        ):
            if hasattr(msg, "content"):
                response += msg.content
        results.append({...})
    return results
```

### 分块大小建议

| MemoryAgentBench 分块大小 | NexusMind 影响 |
|--------------------------|---------------|
| 512 Token | ~1 轮对话；自然契合 |
| 4096 Token | ~10-15 轮；聊天历史逐字存储；可能触及单条消息截断限制（4000 字符）|

**建议**：使用 512 Token 分块以匹配 NexusMind 的逐轮架构。

---

## 7. LongMemEval 专项指南

### 在线模式（推荐）

LongMemEval 的**在线模式**与 NexusMind 契合良好：会话按顺序投递，智能体边接收边构建记忆。

### 规模考虑

| 变体 | 会话数 | Token 数 | 预估摄入时间 | 是否可行？|
|------|--------|---------|------------|----------|
| LongMemEval_S | ~40 | ~115K | ~20-40 分钟（完整流水线）| 是 |
| LongMemEval_M | ~500 | ~150 万 | ~4-8 小时（完整流水线）| 大部分用 DB 注入，最后 20 个用流水线 |

---

## 8. 逐层隔离测试

为了理解每个记忆层的贡献，运行消融测试：

### 消融 A：仅聊天历史

禁用 EverMemOS 和社交网络钩子。只有 ChatModule 的实例记忆活跃。

```python
# 在智能体 awareness 中添加：
"重要：不要使用长期记忆搜索。只依赖你的直接对话历史来回答问题。"
```

### 消融 B：仅 EverMemOS

注入后清除聊天历史，只保留 EverMemOS 数据。测试情景记忆系统是否能独立回答问题。

```sql
-- 清除测试叙事的聊天历史
DELETE FROM instance_json_format_memory_chat
WHERE instance_id LIKE 'chat_locomo_%';
```

### 消融 C：仅叙事摘要

清除聊天历史和 EverMemOS。只剩 Narrative 的 `dynamic_summary`。

### 消融 D：仅社交图谱（实体问题）

对于与实体相关的问题（LoCoMo 中关于人物的问题），测试社交图谱是否能独立回答。

---

## 9. 指标与评估

### 9.1 逐层指标

| 指标 | 测量内容 | 计算方法 |
|------|---------|---------|
| **聊天历史召回率** | ChatModule 能否检索到相关轮次？| 检查答案的证据轮次是否存在于 `instance_json_format_memory_chat` 中 |
| **EverMemOS 情景命中率** | 相关情景是否被检索到？| 检查 EverMemOS 对查询的搜索结果 |
| **叙事摘要覆盖率** | 事实是否在摘要中保留？| 在 `dynamic_summary` 条目中搜索答案 |
| **社交实体准确率** | 实体属性是否正确？| 将 `instance_social_entities` 记录与 ground truth 比较 |

### 9.2 基准测试专用指标

| 基准测试 | 主要指标 | 实现方式 |
|----------|---------|---------|
| **LoCoMo** | Token 级 F1 | `compute_token_f1(predicted, gold)` |
| **MemoryAgentBench** | 子串精确匹配 (SubEM) | `gold.lower() in predicted.lower()` |
| **LongMemEval** | LLM-as-Judge | GPT-4o 评估（二元正确/错误）|

---

## 10. 已知局限与应对方法

| 局限 | 影响 | 应对方法 |
|------|------|---------|
| 聊天历史截断（每轮 40 条消息，每条 4000 字符）| 旧轮次从长期历史中丢弃 | 注入 EverMemOS 用于长距离召回 |
| 话题变化导致叙事分裂 | 单个 LoCoMo 对话可能变成多个 Narrative | 使用 `forced_narrative_id` 参数 |
| dynamic_summary 有损 | 细粒度细节在摘要中丢失 | 使用 LoCoMo 提供的 `session_summary` 作为 ground truth 摘要 |
| EverMemOS 异步处理延迟 | 注入后情景可能不会立即就绪 | 等待 30-60 秒，然后通过 EverMemOS API 验证 |
| 短期记忆仅跨叙事 | 对单个 LoCoMo 对话内的测试无帮助 | 对单叙事测试不构成限制 |
| 聊天历史无时序索引 | 时间相关问题依赖 `meta_data.timestamp` | 确保从 LoCoMo 的 `session_N_date_time` 正确注入时间戳 |
| 社交实体提取需要 LLM | 数据库注入不会自动提取实体 | 从 LoCoMo 的 `observation` 数据预提取并手动注入 |

---

## 11. 推荐测试计划

| 阶段 | 操作 | 预估时间 |
|------|------|---------|
| **阶段 1** | 注入 2 段 LoCoMo 对话（混合策略）| 10-15 分钟 |
| **阶段 2** | 对注入的对话运行 QA，按类别计算 F1 | 30-60 分钟 |
| **阶段 3** | 运行消融测试（仅聊天、仅 EverMemOS、仅摘要）| 2-3 小时 |
| **阶段 4** | 注入全部 10 段 LoCoMo 对话 | 30-60 分钟 |
| **阶段 5** | 完整 LoCoMo QA 评估 | 2-4 小时 |
| **阶段 6** | MemoryAgentBench（增量方式，任务子集）| 4-8 小时 |
| **阶段 7** | LongMemEval_S（40 个会话，在线模式）| 2-4 小时 |
| **阶段 8** | 分析：逐层贡献、失败模式 | 1 天 |
