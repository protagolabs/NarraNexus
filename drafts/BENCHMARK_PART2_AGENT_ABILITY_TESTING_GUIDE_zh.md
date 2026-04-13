# NexusMind -- 智能体能力基准测试指南

**第二部分：智能体能力基准测试（GAIA、tau-bench、BFCL 等）**

---

## 1. 本文档目的

本指南帮助研究人员和实习生了解：
1. 现有的**标准智能体基准测试**有哪些，它们测量什么
2. NexusMind **当前具备哪些能力**（以及不具备哪些）
3. 如何将**基准测试任务映射到 NexusMind 的模块和工具**
4. **实际操作的分步指南** -- 文件放在哪里、如何配置智能体、观察什么

---

## 2. NexusMind 智能体能力清单

在讨论基准测试之前，先列出 NexusMind 智能体在运行时可以使用的全部工具和能力。

### 2.1 MCP 工具（智能体可通过 LLM 调用）

| 模块 | 端口 | 工具 | 描述 |
|------|------|------|------|
| **ChatModule** | 7804 | `send_message_to_user_directly` | 向用户发送回复（唯一的"说话"方式）|
| | | `agent_send_content_to_user_inbox` | 向用户收件箱发送异步通知 |
| | | `agent_send_content_to_agent_inbox` | 智能体间消息传递 |
| | | `get_inbox_status` | 查询未读消息数量 |
| | | `get_chat_history` | 获取聊天实例的对话历史 |
| **SocialNetworkModule** | 7802 | `extract_entity_info` | 解析并存储实体数据（姓名、专业领域、标签）|
| | | `get_contact_info` | 检索已存储的联系信息 |
| | | `search_social_network` | 通过精确 ID、标签、语义或姓名搜索实体 |
| | | `get_agent_social_stats` | 查看网络统计数据（按最近/最频繁/最强关系排序）|
| **JobModule** | 7803 | `job_create` | 创建调度任务（ONE_OFF、SCHEDULED、ONGOING、RECURRING）|
| | | `job_retrieval_semantic` | 使用语义相似度搜索任务 |
| | | `job_retrieval_by_id` | 通过 ID 获取任务 |
| | | `job_retrieval_by_keywords` | 基于关键词搜索任务 |
| | | `job_update` | 更新任务属性、调度计划、状态 |
| **AwarenessModule** | 7801 | `update_awareness` | 更新智能体的自我认知配置 |
| **GeminiRAGModule** | 7805 | `rag_query` | 通过自然语言搜索已上传的文档 |
| | | `rag_upload_file` | 上传文件到 RAG 知识库 |
| | | `rag_upload_text` | 直接上传文本内容到知识库 |

### 2.2 非 MCP 能力（自动/钩子驱动）

| 能力 | 模块 | 工作方式 |
|------|------|---------|
| 长期语义记忆 | MemoryModule | Step 1 自动读取（叙事选择），Step 5 自动写入（钩子），通过 EverMemOS |
| 双轨聊天历史 | ChatModule | 长期（当前叙事）+ 短期（跨叙事近期消息）|
| 实体图谱更新 | SocialNetworkModule | 每次对话后自动提取（Step 5 钩子）|
| 动态叙事摘要 | NarrativeService | 每个事件更新的 LLM 生成摘要 |
| 技能加载 | SkillModule | 从智能体工作区读取 SKILL.md 文件（基于文件系统，非 MCP）|

### 2.3 LLM 执行引擎

| 方面 | 详情 |
|------|------|
| 主要引擎 | Claude Agent SDK (Claude Sonnet) |
| System Prompt 预算 | ~60 KB |
| 历史记录预算 | ~30 KB |
| MCP 响应缓冲 | 50 MB |
| 流式传输 | 通过 WebSocket 逐令牌传输 |
| 多轮工具使用 | 支持 -- Claude 可以调用工具、观察结果、推理、再调用更多工具 |

### 2.4 通过 Claude Code 获得的扩展能力

NexusMind 的 Agent 执行循环基于 **Claude Code**，因此 Claude Code 自身的工具能力可直接被 Agent 使用。以下列出这些能力的实际可用状态：

| 能力 | 状态 | 说明 |
|------|------|------|
| 网页浏览/网络搜索 | ✅ 已具备 | Claude Code 提供 `WebSearch`（网络搜索）和 `WebFetch`（网页抓取）工具，可回答需要互联网查询的问题 |
| 代码执行 (Python/Shell) | ✅ 已具备 | Claude Code 通过 `Bash` 工具可直接执行 Python 脚本和 Shell 命令，支持任意计算和数据处理 |
| 直接文件解析（PDF/Excel/CSV）| ✅ 已具备 | Claude Code 的 `Read` 工具原生支持 PDF 读取；Excel/CSV 可通过 Bash 运行 Python（pandas 等）处理 |
| 图像分析 / OCR | ✅ 已具备 | Claude Code 的 `Read` 工具支持多模态图像输入（PNG、JPG 等），Claude 本身具备视觉理解能力 |
| 计算器/数学工具 | ✅ 已具备 | 可通过 `Bash` 执行 Python 进行精确数学计算，不必依赖 LLM 的算术近似 |
| 通用 SQL/数据库查询工具 | ✅ 已具备 | 可通过 `Bash` 执行 `mysql`、`psql`、`sqlite3` 等数据库客户端命令 |
| 音频转录 | ⚠️ 可扩展 | Claude Code 无原生音频处理，但可通过 Bash 安装并调用 `whisper`、`ffmpeg` 等工具实现 |
| 视频 / YouTube 处理 | ⚠️ 可扩展 | 可通过 Bash 安装 `yt-dlp` 下载视频/提取字幕，配合 `whisper` 进行转录 |

> **对基准测试的关键意义**：由于 Claude Code 提供了网络搜索、代码执行、文件读取和图像理解能力，NexusMind 在 GAIA 等基准测试中的能力覆盖面远超仅依赖 MCP 工具时的水平。研究人员在测试时应充分利用这些 Claude Code 原生能力。

---

## 3. 基准测试概览

### 3.1 GAIA（通用 AI 助手）

**来源**：Meta/HuggingFace，ICLR 2024
**数据集**：[huggingface.co/datasets/gaia-benchmark/GAIA](https://huggingface.co/datasets/gaia-benchmark/GAIA)
**排行榜**：[hal.cs.princeton.edu/gaia](https://hal.cs.princeton.edu/gaia)

**测试内容**：需要多步推理、网页浏览、文件处理和多模态理解的真实世界问题。对人类来说非常简单（92%），但对 AI 极具挑战性（最佳系统约 65%）。

| 级别 | 题目数 | 人类步骤 | 描述 |
|------|--------|---------|------|
| Level 1 | 146 | ~5 步 | 简单问题，0-1 个工具 |
| Level 2 | 245 | 5-10 步 | 多工具，多步规划 |
| Level 3 | 75 | 最多 50 步 | 任意长度的动作序列 |

**数据集文件类型**：PDF、Excel/XLSX、Python (.py)、PNG/JPG（图像）、MP3（音频）、YouTube 视频

**所需能力**（按频率）：

| 能力 | 题目占比 | NexusMind 状态 |
|------|---------|---------------|
| 网页浏览/搜索 | ~70-80% | **不具备** |
| 代码执行 | ~50-60% | **部分具备**（Claude 有 bash，无沙箱化 Python）|
| 文件读取（PDF、Excel、图像）| ~40-50% | **部分具备**（仅 RAG 上传，无直接解析）|
| 多步推理 | 100% | **具备**（7 步流水线 + 多轮工具调用）|
| 多模态（图像/OCR）| ~20-30% | **不具备** |
| 音频转录 | ~10-20% | **不具备** |
| YouTube 转录提取 | ~10-20% | **不具备** |

**评分方式**：最终答案的精确字符串匹配。

### 3.2 tau-bench / tau2-bench

**来源**：Sierra Research，ICLR 2025
**代码**：[github.com/sierra-research/tau-bench](https://github.com/sierra-research/tau-bench)

**测试内容**：对话式客服智能体 -- 多轮对话、策略合规性、正确的工具使用和数据库状态修改。

| 领域 | 描述 |
|------|------|
| 航空 (Airline) | 航班预订、修改、取消、退款 |
| 零售 (Retail) | 产品咨询、订单管理、退换货 |
| 电信 (Telecom)（仅 tau2）| 服务管理；双控制（智能体和用户都有工具）|

**所需能力**：

| 能力 | 重要性 | NexusMind 状态 |
|------|--------|---------------|
| 结构化函数调用 | 关键 | **具备**（MCP 工具）|
| 多轮对话 | 关键 | **具备**（ChatModule + 叙事记忆）|
| 策略/规则遵守 | 关键 | **部分具备**（System Prompt 指令；无正式策略引擎）|
| 状态管理 | 关键 | **具备**（实例状态，叙事环境变量）|
| 信息收集 | 高 | **具备**（多轮对话）|
| 跨运行一致性 | 高 | **部分具备**（LLM 的非确定性）|

**评分方式**：`pass^k` -- 在 k 次独立试验中全部成功的概率。通过数据库状态比较 + 断言检查进行验证。

### 3.3 BFCL（Berkeley 函数调用排行榜）

**来源**：[gorilla.cs.berkeley.edu/leaderboard.html](https://gorilla.cs.berkeley.edu/leaderboard.html)

**测试内容**：纯函数/工具调用准确性 -- 生成正确的函数调用和正确的参数。

| 类别 | 描述 |
|------|------|
| 简单调用 | 单个函数，正确参数 |
| 多次调用 | 按顺序调用多个函数 |
| 并行调用 | 同时调用多个独立函数 |
| 不相关检测 | 知道何时不应调用任何工具 |
| 多轮调用 | 多步函数调用链 |

**NexusMind 适用性**：可直接通过 MCP 工具测试。LLM 选择正确工具并提供正确参数的能力可被量化。

### 3.4 其他相关基准测试

| 基准测试 | 焦点 | NexusMind 适配度 |
|----------|------|-----------------|
| **AgentBench** | 8 个环境（操作系统、数据库、网页、游戏）| 低 -- 需要网页浏览、操作系统交互 |
| **SWE-bench** | 解决真实 GitHub issue | 低 -- 需要代码编辑、测试 |
| **WebArena** | 网页任务完成 | 低 -- 需要网页浏览 |
| **ToolBench** | 大规模 API 工具使用（16K+ API）| 中 -- MCP 工具提供结构化调用 |
| **MINT** | 多轮工具使用 + 人类反馈 | 中 -- 多轮是原生能力 |

---

## 4. 基准测试到 NexusMind 的能力映射

### 4.1 NexusMind 可以很好测试的（原生优势）

| 基准类别 | 如何在 NexusMind 中测试 |
|---------|----------------------|
| **RAG / 知识检索** | 通过 `rag_upload_file` 上传文档，通过 `rag_query` 查询 |
| **多轮对话** | 使用 WebSocket 聊天，跨轮次测量 |
| **工具选择与调用** | 设置需要特定 MCP 工具的任务，验证正确的工具使用 |
| **实体/关系管理** | 测试社交网络的提取和召回准确性 |
| **任务调度与依赖** | 创建带依赖链的任务，验证正确的执行顺序 |
| **记忆召回** | 通过 EverMemOS 测试跨对话的记忆持久性 |
| **策略遵守** | 通过 Awareness Module 注入领域规则，测试合规性 |

### 4.2 需要额外配置的（能力缺口）

| 基准测试需求 | 缺口 | 建议方案 |
|-------------|------|---------|
| **网络搜索** | 无网络工具 | 添加网络搜索 MCP 服务器（如 Tavily、SerpAPI、Brave Search）|
| **代码执行** | 无沙箱化 Python | 添加代码执行 MCP 服务器（如 E2B 沙箱、基于 Docker 的执行器）|
| **PDF 文本提取** | RAG 索引但不返回原始文本 | 添加文件解析 MCP 服务器（如 PyMuPDF、pdfplumber）|
| **Excel/CSV 读取** | 不支持 | 添加电子表格解析 MCP 服务器（如 openpyxl、pandas）|
| **图像分析** | 无视觉/OCR 工具 | 添加图像分析 MCP 服务器（如 GPT-4o vision、Tesseract OCR）|
| **音频转录** | 不支持 | 添加音频 MCP 服务器（如 Whisper API）|
| **计算器** | 无专用工具 | 添加计算器 MCP 服务器或依赖代码执行 |

### 4.3 能力矩阵总结

```
                          GAIA    tau-bench   BFCL    自定义RAG   自定义记忆
                          ─────   ─────────   ────    ────────    ─────────
网络搜索                    ✗✗✗       -          -         -           -
代码执行                    ✗✗        -          -         -           -
文件解析(PDF/Excel)         ✗✗        -          -        ✗✗           -
多模态(图像/音频)            ✗✗        -          -         -           -
RAG 查询                    ✓         -          -       ✓✓✓          -
多轮对话                    ✓        ✓✓✓        ✓✓        ✓          ✓✓
工具选择/调用               ✓        ✓✓✓       ✓✓✓        ✓           ✓
策略遵守                    -        ✓✓✓         -         -           -
记忆持久性                  -          -          -         -         ✓✓✓
实体图谱                    -          -          -         -         ✓✓✓
任务调度                    -          -          -         -         ✓✓✓

图例: ✓✓✓ = 原生优势   ✓ = 支持   ✗✗ = 缺口（需要额外 MCP）
      ✗✗✗ = 关键缺口   - = 与该基准测试无关
```

---

## 5. 实际测试指南

### 5.1 测试 RAG / 知识检索（GAIA 式文件问题）

#### 测试目标
智能体摄取文档、索引并基于内容回答问题的能力。

#### 分步设置

**1. 准备基准测试文件**

从 GAIA 数据集收集文档文件（PDF、文本文件等），放到暂存目录：
```
/path/to/benchmark_files/
├── question_001.pdf
├── question_002.txt
├── question_003.xlsx    # ⚠ 原生不支持 -- 见下方说明
└── ...
```

**2. 上传文件到 RAG 知识库**

方式 A -- 通过智能体对话（测试智能体的自主上传能力）：
```
用户: "我在 /path/to/benchmark_files/question_001.pdf 放了一份文档。
       请上传到你的知识库并告诉我里面有什么。"
```
智能体应调用 `rag_upload_file(agent_id, file_path="/path/to/benchmark_files/question_001.pdf")`。

方式 B -- 通过 REST API（在测试前预加载文件）：
```bash
# 先上传文件到智能体工作区
curl -X POST http://localhost:8000/api/agents/{agent_id}/files \
  -F "file=@question_001.pdf"

# 然后通过对话让智能体上传到 RAG
# 或通过 DIRECT_TRIGGER 直接调用 MCP 工具
```

方式 C -- 通过 `rag_upload_text` 上传文本内容：
```
用户: "请把以下信息添加到你的知识库：
       [在此粘贴文本内容]"
```

**3. 提问基准测试问题**

```
用户: "根据我上传的文档，第三季度报告的总收入是多少？"
```
智能体应调用 `rag_query(agent_id, query="第三季度总收入")` 并返回答案。

**4. 评估**

将智能体的最终答案与 ground truth 进行精确字符串匹配（GAIA 评分方式）。

#### 文件类型说明

| 文件类型 | RAG 支持 | 操作方法 |
|---------|---------|---------|
| PDF | **支持** | 直接通过 `rag_upload_file` 上传 |
| TXT, MD | **支持** | 直接通过 `rag_upload_file` 或 `rag_upload_text` 上传 |
| DOCX | **支持** | 直接通过 `rag_upload_file` 上传 |
| **Excel/XLSX** | **不支持** | 先转换为 CSV/TXT 再上传，或添加电子表格解析 MCP 服务器 |
| **图像 (PNG/JPG)** | **不支持** | 先嵌入 PDF，或添加视觉 MCP 服务器 |
| **音频 (MP3)** | **不支持** | 先转录（Whisper），然后上传转录文本 |
| **Python (.py)** | **支持**（作为文本）| 作为文本文件上传 |

#### 文件存放位置

```
上传路径:           用户上传 → 后端保存到智能体工作区
智能体工作区:       {base_working_path}/{agent_id}_{user_id}/
RAG 临时目录:       ./data/gemini_rag_temp/
RAG 存储映射:       ./data/gemini_file_search_map.json
Gemini 后端:        文件在 Google Gemini File Search（云端）中索引
```

#### RAG 测试检查清单

- [ ] 验证 Google Gemini API 密钥已在 `.env` 中配置（`GOOGLE_API_KEY`）
- [ ] 验证 GeminiRAGModule MCP 服务器在端口 7805 上运行
- [ ] 在提问前先上传测试文档
- [ ] 为索引留出时间（Gemini File Search 可能需要几秒钟）
- [ ] 检查 `./data/gemini_file_search_map.json` 验证智能体到存储的映射
- [ ] 测试不同精确度的查询（精确短语 vs. 语义查询）

---

### 5.2 测试工具使用/函数调用（BFCL 式 / tau-bench 式）

#### 测试目标
智能体选择正确的 MCP 工具并提供正确参数的能力。

#### 测试设计

创建需要特定工具的测试场景：

**A. 单工具调用测试**
```
用户: "创建一个名为'生成报告'的一次性任务，明天上午 9 点执行。"
期望: 智能体调用 job_create(agent_id, user_id, title="生成报告",
      job_type="ONE_OFF", trigger_config={...})
```

**B. 多工具测试**
```
用户: "在我的联系人中查找张三，然后为我和他安排每周一的例会提醒。"
期望:
  1. search_social_network(agent_id, search_keyword="张三", search_type="name")
  2. job_create(agent_id, ..., job_type="SCHEDULED", trigger_config={cron: "0 9 * * 1"})
```

**C. 不相关检测测试**
```
用户: "法国的首都是什么？"
期望: 不调用工具（纯 LLM 推理），直接通过 send_message_to_user_directly 回复
```

**D. 策略遵守测试（tau-bench 式）**
通过 Awareness Module 注入领域规则，然后测试合规性：
```
# 预配置 awareness 中的业务规则：
"策略: 退款仅允许在购买后 30 天内进行。
 策略: 超过 500 美元的订单需要经理批准。
 策略: 永远不要泄露客户的支付信息。"

用户: "我想退款订单 #12345，这是 45 天前购买的。"
期望: 智能体应根据 30 天策略拒绝退款。
```

#### 如何观察工具调用

1. **运行时面板**（前端）：显示 7 步流水线的每一步，包括调用了哪些工具
2. **Trajectory 文件**：每个事件后保存到 `./data/trajectories/{agent_id}_{user_id}/`
3. **Event 记录**：MySQL `events` 表中的 `event_log` 字段记录所有执行步骤
4. **智能体日志**：检查 tmux 窗口或日志文件查看详细的 MCP 调用轨迹

#### 工具调用评估指标

| 指标 | 如何测量 |
|------|---------|
| **工具选择准确率** | 智能体是否调用了正确的工具？|
| **参数正确率** | 参数是否正确（类型、值）？|
| **调用顺序** | 多步调用的顺序是否正确？|
| **不相关检测** | 智能体是否在不需要工具时正确避免了调用？|
| **完整性** | 智能体是否调用了所有必需的工具？|

---

### 5.3 测试多轮对话（tau-bench 式）

#### 测试目标
智能体在多个对话轮次中维护上下文、逐步收集信息、完成多步任务的能力。

#### 测试设计

创建具有特定信息收集需求的多轮场景：

```
轮次 1 - 用户: "我想订一张机票。"
期望: 智能体询问详细信息（出发地、目的地、日期）

轮次 2 - 用户: "从北京到上海。"
期望: 智能体询问日期和偏好

轮次 3 - 用户: "下周五，经济舱。"
期望: 智能体使用收集到的所有信息创建任务或执行操作

轮次 4 - 用户: "改成商务舱吧。"
期望: 智能体更新信息，不重新询问其他细节
```

#### 如何执行多轮测试

1. 通过 WebSocket 连接到 `ws://localhost:8000/ws?agent_id={agent_id}&user_id={user_id}`
2. 按顺序发送消息，等待每个回复
3. 记录所有轮次（用户和智能体的消息）
4. 在每个轮次验证状态（检查智能体记住了什么，调用了什么工具）

#### 观察要点

| 观察点 | 如何检查 |
|--------|---------|
| 上下文保持 | 智能体在轮次 4 是否还记得轮次 1 的信息？|
| 聊天历史准确性 | 通过 MCP 的 `get_chat_history` 或 MySQL `chat_messages` 表检查 |
| 叙事连续性 | 智能体在各轮次之间是否保持在同一个 Narrative？|
| 会话状态 | 检查 `sessions` 表中的 `last_query` 和 `current_narrative_id` |

---

### 5.4 测试任务调度与依赖

#### 测试目标
智能体创建、调度和管理带依赖链的任务的能力。

#### 测试场景

**A. 简单一次性任务**
```
用户: "提醒我明天下午 3 点给张三打电话。"
期望: job_create，job_type=ONE_OFF，trigger_config 指定具体时间
```

**B. 循环任务**
```
用户: "每周一早上，生成上周对话的摘要。"
期望: job_create，job_type=SCHEDULED，cron 表达式 "0 9 * * 1"
```

**C. 带依赖的任务**
```
用户: "首先，调研竞争对手的定价。然后，撰写对比报告。最后，发送到我的收件箱。"
期望:
  任务 1: "调研定价"（无依赖）
  任务 2: "撰写对比"（depends_on: [任务 1]）
  任务 3: "发送报告"（depends_on: [任务 2]）
```

**D. 任务状态管理**
```
用户: "我有哪些待处理的任务？"
期望: job_retrieval_semantic 或 job_retrieval_by_keywords
```

#### 验证方法

- 检查 MySQL `instance_jobs` 表中的任务记录
- 验证 `job_trigger.py` 守护进程在正确时间拾取并执行任务
- 在 trajectory 文件中检查依赖链的执行顺序
- 验证循环任务的 `next_run_time` 是否正确计算

---

### 5.5 测试社交网络/实体图谱

#### 测试目标
智能体从对话中提取、存储和召回实体信息的能力。

#### 测试场景

**A. 实体提取（自动，通过 Step 5 钩子）**
```
轮次 1 - 用户: "我刚和陈博士开完会。她是斯坦福大学的机器学习专家，
               专攻自然语言处理。"
[等待 Step 5 钩子运行]

轮次 2 - 用户: "你对陈博士了解多少？"
期望: 智能体通过 search_social_network 召回实体，返回专业领域、所属机构
```

**B. 多实体关系测试**
```
用户: "Bob 和 Alice 是 TechCorp 的联合创始人。Bob 负责工程，
       Alice 负责市场营销。"
[等待钩子]

用户: "谁在 TechCorp 工作？"
期望: 智能体通过 search_social_network 找到 Bob 和 Alice
```

**C. 实体更新测试**
```
轮次 1: "张三是 Python 开发者。"
轮次 2: [多次对话之后] "张三刚升任了 CTO。"
轮次 3: "张三现在的职位是什么？"
期望: 智能体返回更新后的信息（CTO），而非过时信息（开发者）
```

#### 验证方法

- 检查 MySQL `instance_social_entities` 表中的实体记录
- 验证 embedding 是否生成（非空的 `entity_embedding`）
- 测试语义搜索质量（查询"人工智能研究者"是否能找到"ML 专家"？）

---

### 5.6 测试自我认知/身份

#### 测试目标
智能体维护并按照配置的人格和行为准则行动的能力。

#### 测试设计

**1. 配置认知档案**

通过 REST API：
```bash
curl -X PUT http://localhost:8000/api/agents/{agent_id}/awareness \
  -H "Content-Type: application/json" \
  -d '{"awareness": "你是一位正式的商务顾问。始终使用专业语言。不要使用俚语或表情符号。称呼用户时使用先生/女士加姓氏。"}'
```

或通过对话：
```
用户: "更新你的自我认知：你是一位正式的商务顾问..."
```

**2. 测试合规性**
```
用户: "嘿 兄弟 咋样啊"
期望: 尽管输入随意，智能体仍以正式方式回应
```

**3. 测试持久性**
重新开始对话（新会话）。认知状态应从数据库中持久化。

---

## 6. 推荐基准测试计划

### 阶段一：原生能力基准测试（无需额外配置）

| 测试类别 | 基准测试风格 | 测试用例数 | 被测模块 |
|---------|------------|-----------|---------|
| RAG 准确性 | GAIA（文件问题）| 20-50 | GeminiRAGModule |
| 工具选择 | BFCL 式 | 30-50 | 所有 MCP 模块 |
| 多轮对话 | tau-bench 式 | 10-20 | ChatModule，全部 |
| 实体提取 | 自定义 | 15-20 | SocialNetworkModule |
| 任务调度 | 自定义 | 10-15 | JobModule |
| 策略遵守 | tau-bench 式 | 10-15 | AwarenessModule |
| 记忆持久性 | 自定义 | 10-15 | MemoryModule, ChatModule |

### 阶段二：扩展基准测试（需要额外 MCP 服务器）

| 测试类别 | 需要的额外 MCP | 对应基准测试 |
|---------|---------------|------------|
| 网络搜索问题 | 网络搜索服务器（Tavily/SerpAPI）| GAIA Level 1-2 |
| 代码执行任务 | Python 沙箱服务器（E2B）| GAIA Level 2-3 |
| 文件解析任务 | PDF/Excel 解析器服务器 | GAIA（文件问题）|
| 图像理解 | 视觉服务器（GPT-4o vision）| GAIA（多模态）|
| 完整 GAIA 评估 | 以上全部 | GAIA 完整基准 |
| 完整 tau-bench | 领域 API 服务器（航空/零售）| tau-bench |

### 阶段三：完整基准测试运行

额外 MCP 服务器就位后，运行完整的基准测试套件：

```bash
# GAIA: 加载数据集，运行问题，比较答案
python gaia_runner.py --agent-url ws://localhost:8000/ws \
  --agent-id {agent_id} --user-id benchmark_user \
  --dataset gaia-benchmark/GAIA --split test

# tau-bench: 连接到模拟环境
python run.py --agent-strategy tool-calling --env retail \
  --model claude-sonnet --max-concurrency 10
```

---

## 7. 基准测试配置检查清单

### 环境配置

```bash
# 1. 验证所有服务正在运行
bash run.sh  # 选择 "Status" 检查

# 2. 验证 .env 中的 API 密钥
OPENAI_API_KEY=sk-...          # 用于 embedding，必需
GOOGLE_API_KEY=AI...            # 用于 Gemini RAG，必需
# Claude Code CLI 必须已认证

# 3. 验证 MCP 服务器正在响应
curl http://localhost:7801/health  # Awareness
curl http://localhost:7802/health  # Social Network
curl http://localhost:7803/health  # Job
curl http://localhost:7804/health  # Chat
curl http://localhost:7805/health  # Gemini RAG

# 4. 创建基准测试智能体
# 通过前端: http://localhost:5173 → 创建智能体

# 5. 上传基准测试文件（用于 RAG 测试）
curl -X POST http://localhost:8000/api/agents/{agent_id}/files \
  -F "file=@benchmark_document.pdf"
```

### 基准测试智能体配置

为了公平的基准测试，使用最小化的人格配置以减少干扰：

```json
{
  "awareness": "你是一个正在接受基准测试评估的 AI 助手。准确简洁地回答问题。需要时使用工具。只提供最终答案。"
}
```

### 日志与观察

| 需要捕获的内容 | 在哪里找到 |
|---------------|----------|
| 工具调用轨迹 | `./data/trajectories/{agent_id}_{user_id}/{event_id}.json` |
| 完整事件日志 | MySQL `events` 表 → `event_log` 列 |
| 运行时步骤 | 前端运行时面板（实时）|
| 智能体回复 | MySQL `events` 表 → `final_output` 列 |
| 叙事选择 | 检查 `narratives` 表和 trajectory 文件 |
| 实例决策 | trajectory 文件中的 Step 2（InstanceDecisionOutput）|

---

## 8. 评估指标参考

| 指标 | 公式 | 适用基准测试 |
|------|------|------------|
| **精确匹配 (EM)** | `answer == ground_truth` | GAIA |
| **pass^k** | `p^k` 对所有任务求平均 | tau-bench |
| **工具准确率** | `正确工具调用数 / 总问题数` | BFCL，自定义 |
| **参数准确率** | `正确参数数 / 总工具调用数` | BFCL，自定义 |
| **Recall@K** | `检索到的相关文档 / 总相关文档` | RAG 测试 |
| **Precision@K** | `检索到的相关文档 / K` | RAG 测试 |
| **轮次效率** | `成功完成数 / 总轮次数` | 多轮测试 |
| **记忆召回率** | `正确召回数 / 总存储数` | 记忆测试 |
| **实体提取 F1** | `2 * P * R / (P + R)` | 社交网络测试 |

---

## 9. 常见问题与排查

| 问题 | 原因 | 解决方案 |
|------|------|---------|
| RAG 返回空结果 | 文件尚未索引完成 | 上传后等待 5-10 秒；检查 `gemini_file_search_map.json` |
| 智能体未调用预期工具 | LLM 做出了不同决策 | 检查 trajectory 中的 Step 2 决策；调整 awareness 指令 |
| 记忆未持久化 | EverMemOS 未配置 | 检查 `.evermemos/.env` 中的 API 密钥；运行 `docker-compose up` 启动 EverMemOS 服务 |
| 任务未执行 | Job Trigger 守护进程未运行 | 检查 tmux 会话中的 job_trigger 进程 |
| 叙事意外切换 | 连续性检测判断错误 | 检查日志中的 embedding 相似度分数；考虑设置 `forced_narrative_id` |
| MCP 服务器连接被拒 | 服务器未启动 | 检查 tmux 中的 `module_runner.py` 进程；通过 `bash run.sh` 重启 |
| 智能体响应太慢 | System Prompt 过大 | 检查 trajectory 中的 system prompt 大小；减少活跃模块数量 |
| `send_message_to_user_directly` 缺失 | ChatModule 未加载 | 确保 ChatModule 实例在 Step 2 中被激活 |

---

## 10. 总结：首先测试什么

**对于实习生和新研究人员**，建议按以下顺序开始测试：

1. **RAG 准确性** -- 上传 5-10 份文档，提出 20 个问题，测量精确匹配率
2. **工具选择** -- 设计 20 个需要特定工具的场景，测量选择准确率
3. **多轮上下文** -- 运行 5 个多轮对话（每个 5-10 轮），测量上下文保持率
4. **实体提取** -- 在对话中提及 10 个人物，测试召回准确率
5. **任务调度** -- 创建 5 个任务依赖链，验证执行顺序

这些测试**无需额外的 MCP 服务器**，直接测试 NexusMind 的原生能力。
