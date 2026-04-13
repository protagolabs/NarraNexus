# NexusMind 框架 -- 架构与运行时报告

**第一部分：项目介绍、工作流程与模块详解**

---

## 1. NexusMind 是什么？

NexusMind 是一个**模块化智能体框架**，其核心理念是：智能不是从单个智能体的孤立运行中涌现，而是从智能体之间的*交互与连接*中涌现。与专注于让单个智能体更聪明的传统框架不同，NexusMind 专注于让智能体**互联** -- 为它们赋予持久记忆、社交身份、人际关系、目标驱动的任务系统以及可组合的能力。

> *"一个孤立的智能体只是一个工具。当它拥有了持久记忆、社交身份、人际关系和目标，它就成为了一个**枢纽(nexus)**中的参与者 -- 一个智能是集体属性而非模型属性的网络。"*

### 技术栈

| 层级 | 技术 |
|------|------|
| 编程语言 | Python 3.13+ |
| 前端 | React 19 + TypeScript + Vite + Zustand |
| 后端 | FastAPI 0.115+ |
| 主数据库 | MySQL 8 (Docker) |
| 长期记忆 | EverMemOS (MongoDB + Elasticsearch + Milvus + Redis) |
| 工具协议 | MCP (Model Context Protocol) |
| LLM 适配器 | Claude Agent SDK（主要）、OpenAI、Gemini |
| 部署方式 | 开发环境使用 tmux，生产环境使用 systemd + nginx |

### 核心特性

| 特性 | 描述 |
|------|------|
| **叙事记忆 (Narrative Memory)** | 对话按语义路由到跨会话维护的故事线中，通过主题相似度而非时间顺序进行检索 |
| **热插拔模块 (Hot-Swappable Modules)** | 每个能力（聊天、社交图谱、RAG、任务、技能、记忆）都是独立模块，拥有自己的数据库表、MCP 工具和生命周期钩子 |
| **社交网络 (Social Network)** | 实体图谱，追踪人物、关系、专业领域和交互历史，支持语义搜索 |
| **任务调度 (Job Scheduling)** | 支持一次性、Cron、周期性和持续性任务，具备依赖关系 DAG |
| **RAG 知识库 (RAG Knowledge Base)** | 通过 Gemini File Search 进行文档索引和语义检索 |
| **语义记忆 (Semantic Memory)** | 由 EverMemOS 驱动的长期情景记忆 |
| **执行透明性 (Execution Transparency)** | 每个流水线步骤通过 WebSocket 实时可见 |
| **多 LLM 支持 (Multi-LLM Support)** | 通过统一适配器层支持 Claude、OpenAI 和 Gemini |

---

## 2. 整体架构

```
┌──────────────────────────────────────────────────────────────────────┐
│                         前端 (React 19)                              │
│  ┌──────────┐  ┌─────────────────────┐  ┌─────────────────────────┐ │
│  │ 侧边栏   │  │    聊天面板          │  │    上下文面板            │ │
│  │ (智能体   │  │  (WebSocket 流式)   │  │  标签页: 运行时 | 智能体 │ │
│  │  列表)    │  │  (消息历史)         │  │  配置 | 收件箱 | 任务   │ │
│  │          │  │  (输入框)           │  │  | 技能 | 社交图谱       │ │
│  └──────────┘  └─────────────────────┘  └─────────────────────────┘ │
└───────────────────────────┬──────────────────────────────────────────┘
                            │ WebSocket + REST
                            ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    后端 (FastAPI :8000)                               │
│  路由: /ws (WebSocket) | /api/agents | /api/jobs | /api/inbox ...    │
└───────────────────────────┬──────────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    AgentRuntime (编排器)                              │
│         7 步流水线: 初始化 → 叙事 → 模块 → 执行                       │
│                    → 持久化 → 钩子 → 回调                             │
└───────┬───────────┬──────────────┬───────────────┬───────────────────┘
        │           │              │               │
        ▼           ▼              ▼               ▼
┌─────────────┐ ┌────────┐ ┌─────────────┐ ┌──────────────┐
│  叙事服务    │ │ 模块   │ │  上下文      │ │ 智能体       │
│  Narrative   │ │ 系统   │ │  运行时      │ │ 框架         │
│  Service     │ │ (8个)  │ │ (提示词     │ │ (LLM SDK     │
│ (故事线)     │ │        │ │  构建器)    │ │  适配器)     │
└──────┬───────┘ └───┬────┘ └─────────────┘ └──────────────┘
       │             │
       ▼             ▼
┌─────────────┐ ┌────────────────────────┐
│  MySQL 8    │ │ MCP 服务器              │
│  (主数据库)  │ │ :7801-7805 (每模块一个) │
└─────────────┘ └────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────┐
│  EverMemOS（可选的长期记忆）              │
│  MongoDB + Elasticsearch + Milvus       │
└─────────────────────────────────────────┘
```

---

## 3. 服务启动顺序

执行 `bash run.sh` 并选择 "Run" 后，以下 6 个服务通过 tmux 依次启动：

| 顺序 | 服务 | 端口 | 职责 |
|------|------|------|------|
| 1 | MySQL (Docker) | 3306 | 主关系型数据库 |
| 2 | MCP 服务器 | 7801-7805 | 各模块的工具服务器（通过 `module_runner.py`）|
| 3 | FastAPI 后端 | 8000 | REST API + WebSocket 流式端点 |
| 4 | Job Trigger | -- | 后台守护进程，轮询调度任务 |
| 5 | Module Poller | -- | 实例状态轮询与依赖触发 |
| 6 | React 前端 | 5173 | Vite 开发服务器 |

---

## 4. Agent 运行时 -- 7 步执行流水线

`AgentRuntime` 类（`src/xyz_agent_context/agent_runtime/agent_runtime.py`）是**核心编排器**。每条用户消息或调度任务都会经过严格的 7 步流水线。运行时是一个 `AsyncGenerator`，产出 `ProgressMessage` 对象（在 UI 的运行时面板中可见）和 `AgentTextDelta` 令牌（流式传输到聊天界面）。

```
┌─────────────────────────────────────────────────────────────────────┐
│                     AgentRuntime.run() 流水线                        │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ 初始化阶段                                                   │    │
│  │   Step 0:   初始化                                           │    │
│  │             加载智能体配置，创建 Event，初始化 Session          │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                              │                                      │
│                              ▼                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ 上下文准备阶段                                               │    │
│  │   Step 1:   选择叙事 (Narrative)（查找/创建故事线）            │    │
│  │   Step 1.5: 初始化 Markdown（读取历史对话记录）                │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                              │                                      │
│                              ▼                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ 模块加载阶段                                                 │    │
│  │   Step 2:   加载模块（LLM 决定需要哪些实例）                   │    │
│  │   Step 2.5: 同步实例（持久化到数据库 + Markdown）              │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                              │                                      │
│                              ▼                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ 执行阶段                                                     │    │
│  │   Step 3:   执行路径                                         │    │
│  │             ├─ AGENT_LOOP (99%): LLM 推理 + MCP 工具调用     │    │
│  │             └─ DIRECT_TRIGGER (1%): 跳过 LLM，直接调用 MCP   │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                              │                                      │
│                              ▼                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ 持久化阶段                                                   │    │
│  │   Step 4:   持久化结果                                       │    │
│  │             Trajectory 文件 + Event + Narrative 摘要          │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                              │                                      │
│                              ▼                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ 后处理阶段                                                   │    │
│  │   Step 5:   执行钩子（各模块的事件后处理钩子）                  │    │
│  │   Step 6:   处理钩子回调（依赖触发）                           │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 逐步详解

#### Step 0: 初始化

**目的**：为本轮对话设置执行上下文。

| 子步骤 | 操作 | 输出 |
|--------|------|------|
| 0.1 | 从数据库加载智能体配置 | `ctx.agent_data` |
| 0.2 | 初始化 `ModuleService`（模块加载器）| `ctx.module_service` |
| 0.3 | 创建 `Event` 记录（本轮对话的载体）| `ctx.event` |
| 0.4 | 获取或创建 `Session`（管理跨轮次的连续性）| `ctx.session` |
| 0.5 | 加载智能体自我认知 (Awareness)（人格上下文）| `ctx.awareness` |

#### Step 1: 选择叙事 (Narrative)

**目的**：将用户输入路由到正确的语义故事线。

**Narrative** *不是*简单的聊天线程。它是一个语义主题容器，可以积累事件、追踪活跃的模块实例，并通过**主题相似度**而非时间顺序进行检索。

```
用户输入
    │
    ▼
ContinuityDetector（连续性检测器）: 这条消息属于当前 Narrative 吗？
  （通过 LLM 比较输入与 session.last_query + 当前 narrative 信息）
    │
    ├── 是 → 复用 session.current_narrative_id
    │
    └── 否 → 在所有智能体的 Narrative 中进行向量搜索
                │
                ├── 得分 > 阈值 → 复用已有的 Narrative
                │
                └── 得分 < 阈值 → 创建新 Narrative
                                   （生成名称、embedding、关键词）
```

**输出**：`ctx.narrative_list`（主 Narrative + 最多 K=5 个辅助 Narrative），`ctx.main_narrative`

#### Step 1.5: 初始化 Markdown

**目的**：读取该 Narrative 对应的 Markdown 文件，获取历史对话记录和实例状态。

每个 Narrative 在磁盘上都有一个对应的 `.md` 文件，以人类可读的格式记录对话历史和实例元数据。该文件既是上下文来源，也是调试产物。

**输出**：`ctx.markdown_history`

#### Step 2: 加载模块（核心决策点）

**目的**：通过带有 **Structured Output（结构化输出）** 的 LLM 调用，决定本轮需要哪些模块实例。

LLM 接收以下信息：
- 当前用户输入
- Narrative 当前活跃的实例
- Narrative 的摘要和历史
- 可用的模块元数据（名称、描述、能力）

返回结构化的 `InstanceDecisionOutput`：
```json
{
    "should_create_instances": [
        {"module_class": "ChatModule", "instance_id": "chat_abc12345", ...},
        {"module_class": "JobModule", "instance_id": "job_def67890", ...}
    ],
    "should_remove_instances": ["old_instance_xyz"],
    "execution_path": "agent_loop",
    "execution_reasoning": "用户在提问，需要 LLM 推理"
}
```

对于每个决定的实例，系统会：
1. 创建 `Module` 对象（如 `ChatModule(agent_id, user_id, db_client)`）
2. 将模块绑定到实例（`instance.module = module_object`）
3. 如果模块需要，启动其 MCP Server

**执行路径决策**：
- **AGENT_LOOP**（约 99%）：正常对话 -- LLM 推理并可能调用 MCP 工具
- **DIRECT_TRIGGER**（约 1%）：显式 API 操作 -- 跳过 LLM，直接调用特定 MCP 工具

**输出**：`ctx.load_result`、`ctx.active_instances`、`ctx.module_list`

#### Step 2.5: 同步实例

**目的**：将实例变更持久化到数据库并更新 Markdown 文件。

- 新创建的实例保存到 `module_instances` 表，并通过 `instance_links` 关联到 Narrative
- 移除的实例删除其关联关系
- Markdown 文件更新为当前实例列表和 Mermaid 关系图

#### Step 3: 执行路径（核心执行点）

这是智能体实际"思考"并响应的阶段。

**AGENT_LOOP 路径**（主要路径）：

```
1. 数据收集阶段
   └─ 对每个活跃模块调用 hook_data_gathering(ctx_data)
      ├─ ChatModule:          加载聊天历史到 ctx_data.chat_history
      ├─ SocialNetworkModule: 加载实体上下文到 ctx_data
      ├─ JobModule:           加载活跃任务到 ctx_data
      ├─ MemoryModule:        加载 EverMemOS 记忆到 ctx_data
      └─ ...（每个模块用自己的数据丰富 ctx_data）

2. 上下文合并阶段（ContextRuntime）
   └─ 合并所有模块的 Instructions（按优先级排序）
   └─ 收集所有模块的 MCP Server URL
   └─ 构建完整的 System Prompt：
      ┌──────────────────────────────────────────┐
      │ System Prompt =                           │
      │   叙事信息 (Narrative Info)（主题、摘要）    │
      │ + 辅助叙事摘要                              │
      │ + 模块指令（按优先级排序）                    │
      │ + 短期记忆（跨叙事）                         │
      └──────────────────────────────────────────┘

3. 为 LLM 构建消息数组
   └─ [system prompt] + [长期聊天历史] + [当前用户输入]

4. 调用 Claude Agent SDK
   └─ 连接 MCP 服务器（工具端点）
   └─ 多轮推理循环（LLM 决定何时调用工具）
   └─ 通过 AgentTextDelta 实时流式传输令牌到前端

5. 收集结果
   └─ final_output、execution_steps
```

**DIRECT_TRIGGER 路径**（少见）：
1. 解析 `direct_trigger` 配置（module_class、工具名、参数）
2. 找到目标模块的 MCP Server URL
3. 直接调用 MCP 工具（不涉及 LLM）
4. 返回工具结果

**输出**：`ctx.execution_result`（final_output、execution_steps、agent_loop_response）

#### Step 4: 持久化结果

**目的**：保存所有执行产物。

| 子步骤 | 操作 |
|--------|------|
| 4.1 | 记录 **Trajectory**（执行轨迹文件，写入磁盘）|
| 4.2 | 更新 Markdown 的统计信息 |
| 4.3 | 更新 **Event** 记录：设置 `final_output`、`event_log`、`module_instances` |
| 4.4 | 更新 **Narrative**：追加 `event_id`，重新生成 `dynamic_summary`（LLM 生成的每事件摘要）|
| 4.5 | 更新 Session：设置 `last_query` 用于下一轮连续性检测 |

#### Step 5: 执行钩子

**目的**：在此运行各模块的后处理逻辑。

```
对每个活跃模块：
    调用 module.hook_after_event_execution(params)
    │
    ├─ SocialNetworkModule: 从对话中提取实体信息，
    │                        更新实体图谱
    ├─ MemoryModule:        将对话写入 EverMemOS
    │                        用于长期情景记忆
    ├─ JobModule:           LLM 分析结果，更新任务状态
    └─ ChatModule:          将消息持久化到聊天历史表
```

每个钩子可以返回**回调请求** -- 因为其依赖已满足而应该被触发的实例。

**输出**：`hook_callback_results`

#### Step 6: 处理钩子回调

**目的**：处理基于依赖关系的实例激活。

```
对每个回调请求：
    1. 获取目标实例的依赖项
    2. 检查：所有依赖是否已完成？
       ├── 是 → 在后台生成一个新的 AgentRuntime.run()
       │         使用 working_source=CALLBACK
       │         （异步，非阻塞）
       └── 否 → 跳过（等待其他依赖完成）
```

这使得**链式执行**成为可能：任务 A 完成后，解除对任务 B 的阻塞，任务 B 随即自动运行。

---

## 5. 模块系统 -- 可插拔、零耦合的能力组件

### 设计原则

1. **模块之间不互相导入** -- 绝对零耦合
2. **私有包**用于内部实现（`_module_impl/`）
3. **集中注册**通过 `MODULE_MAP` 字典
4. **新增模块无需修改任何已有模块**
5. **每个模块拥有**：自己的数据库表、MCP 服务器、指令、数据收集钩子

### 模块基类：`XYZBaseModule`

所有模块都继承这个抽象基类：

```python
class XYZBaseModule(ABC):
    # 配置
    def get_config() -> ModuleConfig               # 模块元数据（名称、优先级等）

    # 生命周期钩子
    async def hook_data_gathering(ctx_data)         # 加载数据到上下文（Step 3）
    async def hook_after_event_execution(params)    # 后处理（Step 5）

    # 指令
    async def get_instructions(ctx_data) -> str     # 贡献到 System Prompt 的指令

    # MCP 服务器
    async def get_mcp_config() -> MCPServerConfig   # MCP 服务器配置（URL、名称）
    def create_mcp_server() -> MCPServer            # 创建 MCP 服务器实例

    # 数据库
    async def init_database_tables()                # 创建模块专有表
    def get_table_schemas() -> List[str]            # SQL CREATE TABLE 语句

    # 实例管理
    def get_instance_object_candidates(**kwargs)     # 候选实例
    def create_instance_object(**kwargs)             # 创建实例
```

### 模块注册表

```python
# src/xyz_agent_context/module/__init__.py
MODULE_MAP = {
    "MemoryModule":        MemoryModule,         # 最高优先级
    "AwarenessModule":     AwarenessModule,
    "BasicInfoModule":     BasicInfoModule,
    "ChatModule":          ChatModule,
    "SocialNetworkModule": SocialNetworkModule,
    "JobModule":           JobModule,
    "GeminiRAGModule":     GeminiRAGModule,
    "SkillModule":         SkillModule,
}
```

### 8 大模块一览

| # | 模块 | 实例前缀 | MCP 端口 | 有 MCP 工具 | 描述 |
|---|------|---------|----------|------------|------|
| 1 | **MemoryModule** | -- | -- | 否 | EverMemOS 语义记忆；最高优先级，数据收集阶段最先运行 |
| 2 | **AwarenessModule** | `aware_` | 7801 | 是 | 智能体人格、目标、行为准则、自我认知 |
| 3 | **BasicInfoModule** | `basic_` | -- | 否 | 静态智能体信息（名称、角色、创建者识别）|
| 4 | **ChatModule** | `chat_` | 7804 | 是 | 多用户聊天历史、收件箱系统、双轨记忆（长期+短期）|
| 5 | **SocialNetworkModule** | `social_` | 7802 | 是 | 实体图谱：人物、组织、关系、专业领域、交互历史 |
| 6 | **JobModule** | `job_` | 7803 | 是 | 任务调度（一次性、Cron、周期性、持续性）带依赖 DAG |
| 7 | **GeminiRAGModule** | `rag_` | 7805 | 是 | 基于 Google Gemini File Search 的 RAG |
| 8 | **SkillModule** | -- | -- | 否 | 三级技能管理（智能体级、用户级、叙事级）|

---

## 6. 深入剖析：记忆系统

NexusMind 拥有**四层记忆架构**。这是该框架最重要的差异化特性之一。

```
┌─────────────────────────────────────────────────────────────────┐
│                       记忆架构                                    │
├──────────┬──────────────────────────────────────────────────────┤
│ 层级     │ 描述                                                  │
├──────────┼──────────────────────────────────────────────────────┤
│ 第一层   │ 长期记忆 (EverMemOS)                                   │
│          │ 跨对话的语义记忆                                        │
│          │ 存储: MongoDB + Elasticsearch + Milvus + Redis         │
│          │ 检索方式: 主题相似度（embedding 搜索）                    │
│          │ 写入时机: 每个事件之后（Step 5 钩子）                     │
│          │ 读取时机: 叙事选择阶段（Step 1）                         │
├──────────┼──────────────────────────────────────────────────────┤
│ 第二层   │ 聊天历史（双轨）                                        │
│          │ 长期轨道: 当前 Narrative 的完整对话历史                   │
│          │   → 作为普通消息注入                                     │
│          │ 短期轨道: 其他 Narrative 的近期对话                      │
│          │   → 注入到 System Prompt                                │
│          │ 存储: MySQL (chat_messages, chat_history 表)            │
│          │ 管理者: ChatModule + EventMemoryModule                  │
├──────────┼──────────────────────────────────────────────────────┤
│ 第三层   │ 叙事记忆（事件序列）                                     │
│          │ Narrative 内的事件序列                                   │
│          │ 每个事件: 输入、输出、使用的模块、时间戳                   │
│          │ 动态摘要: LLM 为每个事件生成的摘要                        │
│          │ 存储: MySQL (events 表)                                 │
├──────────┼──────────────────────────────────────────────────────┤
│ 第四层   │ 社交记忆（实体图谱）                                     │
│          │ 人物、组织、关系、专业领域                                │
│          │ 沟通画像、交互历史                                       │
│          │ 检索方式: 基于实体描述的语义搜索                          │
│          │ 更新时机: 每个事件之后（Step 5 钩子）                     │
│          │ 存储: MySQL (social_network_entities 表)                │
└──────────┴──────────────────────────────────────────────────────┘
```

### MemoryModule 生命周期

```
Step 1（叙事选择）:
    ┌─ search_evermemos(query, top_k=10)
    │  在所有已存储的情景中进行向量搜索
    └─ 返回相关记忆 → 注入为辅助叙事内容

Step 3（数据收集）:
    ┌─ hook_data_gathering(ctx_data)
    │  将检索到的语义记忆注入 ctx_data
    └─ 记忆在 System Prompt 中以 "Related Content" 形式出现

Step 5（执行后钩子）:
    ┌─ write_to_evermemos(input_content, final_output, narrative_id)
    │  存储对话用于未来的情景记忆
    └─ EverMemOS 处理: 边界检测 → 情景提取 → embedding 生成
```

### 双轨聊天记忆（ChatModule）

```
长期记忆:
    当前 Narrative 的完整对话历史
    → 作为标准 user/assistant 消息对传递
    → 提供深度主题上下文

短期记忆:
    来自其他 Narrative 的近期对话（跨主题）
    → 作为摘要部分注入到 System Prompt
    → 每条消息截断为 200 字符
    → 提供对用户近期活动的广泛感知
```

---

## 7. 深入剖析：叙事系统

### 什么是 Narrative（叙事）？

Narrative 是一个**语义主题线程** -- 不是聊天室，不是记忆容器，而是一个**路由索引**，它：
- 通过主题对相关对话进行分组（通过 embedding 相似度）
- 追踪该主题上哪些模块实例是活跃的
- 随时间积累事件
- 维护动态摘要（每个事件更新一次）

### Narrative 数据模型

```
Narrative
├── id: string                         （唯一标识符）
├── agent_id: string                   （所属智能体）
├── narrative_info
│   ├── name: string                   （"项目规划"、"Python 帮助" 等）
│   ├── description: string
│   └── current_summary: string        （主题的最新摘要）
│
├── active_instances: List[Instance]   （当前活跃的模块实例）
├── instance_history_ids: List[str]    （已完成/归档的实例）
│
├── event_ids: List[str]               （按时间顺序排列的事件）
├── dynamic_summary: List[Entry]       （每事件 LLM 生成的摘要）
│
├── routing_embedding: List[float]     （1536 维向量，用于相似度搜索）
├── topic_keywords: List[str]          （搜索关键词）
├── topic_hint: string                 （主题描述）
│
└── env_variables: Dict[str, Any]      （叙事级状态变量）
```

### Narrative 选择流程

```
输入: user_input（例如："下周二安排和 John 的会议"）

步骤 1: 连续性检测
    使用 LLM 与 session.last_query 比较
    "这条消息和上一条消息是同一个主题吗？"
    │
    ├── 同一主题 → 复用 session.current_narrative_id
    │
    └── 不同主题 → 进入步骤 2

步骤 2: 向量搜索
    为 user_input 生成 embedding
    在所有智能体的 Narrative 中按余弦相似度搜索
    │
    ├── 最佳匹配得分 > 阈值
    │   → 复用该 Narrative
    │
    └── 没有好的匹配
        → 创建新 Narrative
           ├── LLM 生成: 名称、描述、关键词
           ├── 从描述计算 embedding
           └── 保存到数据库

步骤 3: 加载辅助 Narrative
    Top-K（最多 5 个）相似 Narrative 作为上下文加载
    它们的摘要出现在 System Prompt 中

输出: main_narrative + auxiliary_narratives
```

---

## 8. 深入剖析：社交网络模块

社交网络模块维护一个**实体图谱**，追踪人物、组织及其关系。

### 实体数据模型

```
SocialNetworkEntity
├── entity_id: string
├── agent_id: string
├── name: string                       （"张三"）
├── type: string                       （PERSON, ORGANIZATION, EVENT, CONCEPT）
├── description: string
├── expertise: List[string]            （["Python", "机器学习"]）
├── contact_info: Dict[str, str]       （邮箱、电话等）
├── communication_persona: string      （"直接且技术化"）
├── related_entities: List[string]     （关联人物的实体 ID）
├── interaction_count: int
├── last_interaction_date: datetime
├── entity_embedding: List[float]      （用于语义搜索）
└── keywords: List[string]
```

### MCP 工具

| 工具 | 用途 |
|------|------|
| `extract_entity_info()` | 从对话中解析并存储实体数据 |
| `recall_entity()` | 从图谱中检索特定实体 |
| `search_social_network()` | 在所有实体中进行语义搜索 |

### 更新流程（Step 5 钩子）

```
每次对话后:
1. SocialNetworkModule.hook_after_event_execution() 运行
2. LLM 分析对话中提到的实体
3. 对每个提到的实体：
   ├── 如果是新实体：创建实体记录
   │   ├── 提取名称、类型、专业领域
   │   ├── 生成 embedding
   │   └── 保存到 social_network_entities 表
   │
   └── 如果已存在：更新实体
       ├── 合并新的专业领域/信息
       ├── 增加 interaction_count
       ├── 更新 last_interaction_date
       └── 刷新 embedding
```

---

## 9. 深入剖析：任务模块

任务模块提供**带依赖链的任务调度**。

### 任务类型

| 类型 | 描述 | 示例 |
|------|------|------|
| **ONE_SHOT** | 执行一次（立即或在未来某个时间）| "总结这份文档" |
| **CRON** | 按 Cron 表达式重复 | "每周一上午 9 点检查邮件" |
| **PERIODIC** | 按固定间隔重复 | "每 6 小时检查服务器状态" |
| **CONTINUOUS** | 无固定间隔地重复 | "持续监控这个 API 端点" |

### 任务数据模型

```
JobModel
├── id: string
├── agent_id: string
├── user_id: string
├── name: string                       （"周报生成器"）
├── description: string
├── job_type: JobType                  （ONE_SHOT, CRON, PERIODIC, CONTINUOUS）
├── status: JobStatus                  （PENDING, RUNNING, COMPLETED, FAILED）
├── trigger_config: TriggerConfig      （调度计划、间隔、Cron 表达式）
├── parameters: Dict[str, Any]         （任务专有参数）
├── execution_results: List[Result]    （历史执行记录）
├── last_executed_at: datetime
├── next_run_time: datetime
└── dependencies: List[str]            （该任务依赖的其他任务 ID）
```

### MCP 工具

| 工具 | 用途 |
|------|------|
| `job_create()` | 创建新的调度任务 |
| `job_retrieval_semantic()` | 通过语义查询搜索任务 |
| `job_retrieval_by_id()` | 通过 ID 获取任务 |
| `job_retrieval_by_keywords()` | 通过关键词搜索任务 |

### 执行流程

```
后台: job_trigger.py 守护进程（每 60 秒轮询一次）
    │
    ├── 检查所有任务：status=PENDING/RUNNING，next_run_time <= 当前时间
    │
    └── 对每个就绪任务：
        1. AgentRuntime.run(working_source=JOB, job_instance_id=...)
        2. 智能体执行任务（完整的 7 步流水线）
        3. JobModule.hook_after_event_execution():
           └── LLM 分析执行结果
               ├── 更新任务状态（COMPLETED, FAILED 等）
               ├── 记录执行结果
               └── 计算 next_run_time（对于循环任务）
        4. 检查依赖：
           └── 如果依赖的任务现在已解除阻塞 → 将它们排入队列
```

---

## 10. 深入剖析：自我认知模块

自我认知模块定义了智能体的**人格、目标和行为准则**。

### 它提供什么

| 方面 | 描述 |
|------|------|
| **身份 (Identity)** | 智能体名称、角色、人格描述 |
| **目标 (Goals)** | 当前目标和优先级 |
| **行为准则 (Guidelines)** | 行为规则、语气风格、约束条件 |
| **自我认知 (Self-Awareness)** | 智能体对自身及其能力的了解 |

### 运行时集成

- **Step 0**：从 `instance_awareness` 表加载自我认知内容
- **Step 3（数据收集）**：自我认知注入到 System Prompt 的顶部
- **MCP 工具（端口 7801）**：允许智能体动态查询/更新自身的认知状态

---

## 11. 深入剖析：聊天模块

聊天模块管理**多用户对话历史**，采用**双轨记忆**系统。

### 双轨记忆架构

```
┌──────────────────────────────────────────────────────┐
│                 聊天模块记忆                            │
├─────────────────────────┬────────────────────────────┤
│    长期轨道              │    短期轨道                  │
├─────────────────────────┼────────────────────────────┤
│ 当前 Narrative 的        │ 来自其他 Narrative 的        │
│ 完整对话历史             │ 近期对话                     │
│                         │ （跨主题上下文）              │
│                         │                             │
│ 注入方式：               │ 注入方式：                   │
│ 标准 user/assistant      │ System Prompt 中的           │
│ 消息对                   │ 摘要部分                     │
│                         │                             │
│ 不截断                   │ 每条消息截断为 200 字符       │
│ （Claude SDK 管理         │ 按实例分组，                  │
│  上下文预算）             │ 带相对时间戳                  │
└─────────────────────────┴────────────────────────────┘
```

### MCP 工具（端口 7804）

聊天模块还提供收件箱功能，允许用户接收来自智能体和其他智能体的消息。

---

## 12. 深入剖析：GeminiRAG 模块

GeminiRAG 模块使用 Google Gemini 的 File Search API 提供**基于文档的检索增强生成**。

### 工作原理

1. **上传**：文档通过 Gemini API 上传并索引
2. **查询**：当智能体需要已上传文档中的信息时，使用 RAG 工具
3. **检索**：Gemini File Search 返回相关段落
4. **生成**：智能体将检索到的段落融入其回复中

### MCP 工具（端口 7805）

通过 Gemini File Search API 提供文档上传、搜索和管理工具。

---

## 13. 深入剖析：技能模块

技能模块管理一个**三级技能系统**，用于组织智能体的能力。

| 层级 | 范围 | 示例 |
|------|------|------|
| **智能体级** | 对该智能体的所有叙事可用 | "总结文本"、"翻译" |
| **用户级** | 仅在与特定用户交互时可用 | 自定义偏好 |
| **叙事级** | 仅在特定叙事/主题内可用 | 特定主题的工具 |

技能模块没有自己的 MCP 服务器 -- 它通过在 System Prompt 中列出可用技能来发挥作用。

---

## 14. 实例系统 -- 运行时绑定

### 什么是模块实例？

**模块实例 (Module Instance)** 是模块在 Narrative 中的运行时绑定。可以把模块想象成"部门"（如人力资源、工程部），而实例则是该部门对某个项目（Narrative）的具体"委派"。

```
Module（类级能力）
    │
    └── Instance（到 Narrative 的运行时绑定）
        ├── instance_id: "{prefix}_{uuid8}"（例如 "chat_a1b2c3d4"）
        ├── module_class: "ChatModule"
        ├── status: ACTIVE | IN_PROGRESS | BLOCKED | COMPLETED | FAILED | CANCELLED | ARCHIVED
        ├── config: Dict（实例专有配置）
        ├── dependencies: List[str]（该实例依赖的其他实例 ID）
        └── module: XYZBaseModule（绑定的模块对象，运行时设置）
```

### 实例创建模式

| 模式 | 创建时机 | 每智能体实例数 | 示例 |
|------|---------|--------------|------|
| **智能体级** | 创建智能体时 | 1 | AwarenessModule、BasicInfoModule |
| **叙事级** | 用户在某个叙事中开始聊天时 | 每用户每叙事 1 个 | 用户 Alice 的 ChatModule |
| **任务级** | 每创建一个任务时 | 每叙事多个 | JobModule（每个任务 = 1 个实例）|

### 实例生命周期

```
Step 2:  LLM 决定需要该实例 → 创建（status=ACTIVE）
Step 2.5: 持久化到数据库，关联到 Narrative
Step 3:  模块执行（状态可能更新）
Step 5:  钩子运行，可能产生回调请求
Step 6:  依赖满足 → 在后台生成新的执行
...
最终: status → COMPLETED 或 ARCHIVED
```

---

## 15. 上下文运行时 -- 提示词构建器

`ContextRuntime` 类（`src/xyz_agent_context/context_runtime/context_runtime.py`）负责组装发送给 LLM 的最终提示词。它在 Step 3 内部运行。

### 组装流程

```
步骤 0: 初始化 ContextData
    └─ agent_id、user_id、input_content、narrative_id、working_source

步骤 1: 提取叙事数据
    └─ 加载主题信息、辅助叙事摘要

步骤 2: 从所有模块实例收集数据
    └─ 对每个模块调用 hook_data_gathering()
       ├─ ctx_data.chat_history       ← ChatModule
       ├─ ctx_data.awareness          ← AwarenessModule
       ├─ ctx_data.extra_data["..."]  ← SocialNetworkModule、JobModule 等
       └─ ctx_data.semantic_memories  ← MemoryModule

步骤 3: 构建模块指令（按 module_class 去重）
    └─ 对每个唯一模块调用 get_instructions(ctx_data)
    └─ 按优先级排序

步骤 4: 构建完整的 System Prompt
    ┌──────────────────────────────────┐
    │  第 1 部分: 叙事信息             │  （主题、摘要）
    │  第 2 部分: 辅助叙事             │  （相关主题摘要 + EverMemOS 内容）
    │  第 3 部分: 模块指令             │  （按优先级排序，来自所有活跃模块）
    │  第 4 部分: 短期记忆             │  （跨叙事的近期对话）
    └──────────────────────────────────┘

步骤 5: 构建消息数组
    [
        { role: "system",    content: <完整 System Prompt> },
        { role: "user",      content: <历史消息 1> },
        { role: "assistant", content: <历史消息 2> },
        ...                           （长期聊天历史）
        { role: "user",      content: <当前用户输入> }
    ]

    MCP URLs: { "chat_module": "http://127.0.0.1:7804/sse", ... }
```

### 消息截断策略

- **单条消息上限**：每条消息 4000 字符（防止一次大量粘贴消耗上下文预算）
- **总体预算**：由 Claude Agent SDK 的 `MAX_HISTORY_LENGTH` 管理
- **短期记忆**：跨叙事摘要中每条消息 200 字符

---

## 16. 智能体框架 -- LLM 适配器

NexusMind 通过统一的适配器层支持多个 LLM 提供商。

| 适配器 | 文件 | 主要用途 | 协议 |
|--------|------|---------|------|
| **ClaudeAgentSDK** | `xyz_claude_agent_sdk.py` | 核心智能体推理 + 工具调用 | Claude Code SDK，流式，多轮 |
| **OpenAIAgentsSDK** | `openai_agents_sdk.py` | Embedding、实体分析、摘要 | OpenAI API |
| **GeminiAPI** | `gemini_api_sdk.py` | RAG 文件搜索 | Google Gemini API |

### Claude Agent SDK 集成（主要）

Claude Agent SDK 是主要的执行引擎。在 Step 3（Agent Loop）期间，系统：
1. 传入完整的 System Prompt + 消息历史
2. 连接 MCP 服务器作为工具端点
3. Claude 对查询进行推理并决定何时调用工具
4. 通过 `AgentTextDelta` 实时流式输出令牌
5. 支持多轮工具调用（Claude 可以调用工具、观察结果，然后进一步推理）

---

## 17. 数据流总结 -- 端到端示例

```
用户输入 "每周一为 John 安排一份周报"
                    │
                    ▼
[前端] WebSocket 消息发送到后端
                    │
                    ▼
[Step 0] 创建 Event，加载智能体配置，初始化 Session
                    │
                    ▼
[Step 1] ContinuityDetector: 检测到新主题
         向量搜索: 没有匹配的 Narrative
         → 创建新 Narrative "周报调度"
                    │
                    ▼
[Step 1.5] 为新 Narrative 初始化 Markdown 文件
                    │
                    ▼
[Step 2] LLM 决策：
         ├─ ChatModule 实例（用于对话）
         ├─ JobModule 实例（检测到调度需求）
         ├─ SocialNetworkModule 实例（提到了 "John"）
         └─ execution_path: AGENT_LOOP
                    │
                    ▼
[Step 2.5] 将 3 个新实例持久化到数据库
                    │
                    ▼
[Step 3] 数据收集：
         ├─ ChatModule: 无历史记录（新叙事）
         ├─ JobModule: 无已有任务
         └─ SocialNetworkModule: 在实体图谱中查找 "John"

         上下文构建：
         └─ 构建 System Prompt + 消息数组

         Agent Loop (Claude)：
         ├─ 对请求进行推理
         ├─ 通过 MCP 调用 JobModule.job_create()
         │   └─ 创建 CRON 任务: "周报"，计划: "0 9 * * 1"
         ├─ 通过 MCP 调用 SocialNetworkModule.recall_entity("John")
         │   └─ 检索 John 的信息
         └─ 生成回复: "我已创建了一个周报任务..."
                    │
                    ▼
[Step 4] 持久化：
         ├─ Event 保存，含 final_output
         ├─ Narrative 更新，含 event_id + dynamic_summary
         └─ Trajectory 文件写入
                    │
                    ▼
[Step 5] 钩子：
         ├─ SocialNetworkModule: 更新 John 的 interaction_count
         ├─ MemoryModule: 写入 EverMemOS
         └─ JobModule: 分析结果，确认任务状态
                    │
                    ▼
[Step 6] 无需触发回调
                    │
                    ▼
[前端] 用户看到流式回复 + 运行时面板显示所有步骤
```

---

## 18. 关键设计模式

| 模式 | 使用位置 | 目的 |
|------|---------|------|
| **模块独立性** | 模块系统 | 模块间零耦合；私有包 |
| **基于钩子的集成** | 模块生命周期 | `hook_data_gathering` + `hook_after_event_execution` |
| **仓储模式 (Repository Pattern)** | 数据访问层 | 类型安全的 CRUD；批量加载解决 N+1 问题 |
| **结构化输出 (Structured Output)** | Step 2（实例决策）| LLM 返回保证符合 JSON Schema 的结构 |
| **异步优先 (Async-First)** | 整个代码库 | `AsyncDatabaseClient`、`AsyncGenerator`、`asyncio` |
| **依赖注入 (Dependency Injection)** | AgentRuntime | 可选服务，便于测试/自定义 |
| **语义路由 (Semantic Routing)** | Narrative 选择 | 基于 Embedding 相似度，而非关键词匹配 |
| **双轨记忆 (Dual-Track Memory)** | ChatModule | 长期（完整历史）+ 短期（跨叙事）|

---

## 19. 关键文件位置

| 文件/目录 | 用途 |
|----------|------|
| `src/xyz_agent_context/agent_runtime/agent_runtime.py` | 主 7 步编排器 |
| `src/xyz_agent_context/agent_runtime/_agent_runtime_steps/` | 各步骤的具体实现 |
| `src/xyz_agent_context/module/base.py` | `XYZBaseModule` 基类 |
| `src/xyz_agent_context/module/__init__.py` | `MODULE_MAP` 注册表 |
| `src/xyz_agent_context/module/memory_module/` | EverMemOS 集成 |
| `src/xyz_agent_context/module/chat_module/` | 聊天历史 + 双轨记忆 |
| `src/xyz_agent_context/module/social_network_module/` | 实体图谱 |
| `src/xyz_agent_context/module/job_module/` | 任务调度 + 依赖 DAG |
| `src/xyz_agent_context/module/awareness_module/` | 智能体人格/目标 |
| `src/xyz_agent_context/module/gemini_rag_module/` | 基于 Gemini File Search 的 RAG |
| `src/xyz_agent_context/module/skill_module/` | 三级技能管理 |
| `src/xyz_agent_context/module/basic_info_module/` | 静态智能体信息 |
| `src/xyz_agent_context/narrative/narrative_service.py` | Narrative CRUD + 选择 |
| `src/xyz_agent_context/narrative/event_service.py` | Event CRUD |
| `src/xyz_agent_context/context_runtime/context_runtime.py` | System Prompt 构建器 |
| `src/xyz_agent_context/agent_framework/` | LLM SDK 适配器 |
| `src/xyz_agent_context/repository/base.py` | 通用仓储模式 |
| `src/xyz_agent_context/schema/` | 所有 Pydantic 模型（集中管理）|
| `backend/main.py` | FastAPI 入口点 |
| `backend/routes/websocket.py` | WebSocket 流式传输 |
| `frontend/src/` | React 19 UI 组件 |
| `run.sh` | 统一部署入口 |
| `docker-compose.yaml` | MySQL Docker 配置 |
| `.evermemos/` | EverMemOS（git submodule）|

---

## 20. 总结：NexusMind 的差异化优势

| 维度 | 传统智能体框架 | NexusMind |
|------|--------------|-----------|
| **关注点** | 让单个智能体更聪明 | 让智能体互联 |
| **记忆** | 会话级，对话间遗忘 | 多层次：EverMemOS（长期）+ 聊天（双轨）+ 叙事（事件序列）+ 社交（实体图谱）|
| **组织方式** | 扁平的对话线程 | 带路由 Embedding 的语义叙事 |
| **能力** | 固定工具集 | 零耦合的热插拔模块 |
| **任务** | 一次性函数调用 | 支持依赖关系的任务 DAG，含 Cron/周期/持续调度 |
| **社交** | 无社交感知 | 带关系、专业领域、沟通画像的实体图谱 |
| **透明性** | 黑盒 | 每个步骤实时可见（7 步流水线 + 运行时面板）|
| **可扩展性** | 单用户 | 多用户隔离（按 agent_id + user_id）|
