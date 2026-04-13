# NexusMind Framework -- Architecture & Runtime Report

**Part 1: Introduction, Working Process, and Module Breakdown**

---

## 1. What Is NexusMind?

NexusMind is a **modular agent framework** where intelligence emerges from agent *interaction and connection*, not isolation. Unlike single-agent frameworks that focus on making an individual agent smarter, NexusMind focuses on making agents **connected** -- equipping them with persistent memory, social identity, relationships, goal-driven task systems, and composable capabilities.

> *"An agent in isolation is a tool. An agent with persistent memory, social identity, relationships, and goals becomes a participant in a **nexus** -- a network where intelligence is a collective property, not a model property."*

### Tech Stack

| Layer        | Technology                                                      |
|--------------|-----------------------------------------------------------------|
| Language     | Python 3.13+                                                    |
| Frontend     | React 19 + TypeScript + Vite + Zustand                          |
| Backend      | FastAPI 0.115+                                                  |
| Primary DB   | MySQL 8 (Docker)                                                |
| Long-term Memory | EverMemOS (MongoDB + Elasticsearch + Milvus + Redis)        |
| Tool Protocol| MCP (Model Context Protocol)                                    |
| LLM Adapters | Claude Agent SDK (primary), OpenAI, Gemini                      |
| Deployment   | tmux-based dev, systemd + nginx for production                  |

### Key Features

| Feature | Description |
|---------|-------------|
| **Narrative Memory** | Conversations are routed into semantic storylines maintained across sessions, retrieved by topic similarity rather than chronological order |
| **Hot-Swappable Modules** | Each capability (chat, social graph, RAG, jobs, skills, memory) is a standalone module with its own DB tables, MCP tools, and lifecycle hooks |
| **Social Network** | Entity graph tracking people, relationships, expertise, and interaction history with semantic search |
| **Job Scheduling** | One-shot, cron, periodic, and continuous tasks with dependency DAGs |
| **RAG Knowledge Base** | Document indexing and semantic retrieval via Gemini File Search |
| **Semantic Memory** | Long-term episodic memory powered by EverMemOS |
| **Execution Transparency** | Every pipeline step visible in real time via WebSocket |
| **Multi-LLM Support** | Claude, OpenAI, and Gemini via a unified adapter layer |

---

## 2. High-Level Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                         Frontend (React 19)                          │
│  ┌──────────┐  ┌─────────────────────┐  ┌─────────────────────────┐ │
│  │ Sidebar  │  │    Chat Panel       │  │    Context Panel        │ │
│  │ (Agent   │  │  (WebSocket stream) │  │  Tabs: Runtime | Agent  │ │
│  │  List)   │  │  (Message history)  │  │  Config | Inbox | Jobs  │ │
│  │          │  │  (Input field)      │  │  | Skills | Social Graph│ │
│  └──────────┘  └─────────────────────┘  └─────────────────────────┘ │
└───────────────────────────┬──────────────────────────────────────────┘
                            │ WebSocket + REST
                            ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    Backend (FastAPI :8000)                            │
│  Routes: /ws (WebSocket) | /api/agents | /api/jobs | /api/inbox ...  │
└───────────────────────────┬──────────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    AgentRuntime (Orchestrator)                        │
│         7-Step Pipeline: Init → Narrative → Modules → Execute        │
│                         → Persist → Hooks → Callbacks                │
└───────┬───────────┬──────────────┬───────────────┬───────────────────┘
        │           │              │               │
        ▼           ▼              ▼               ▼
┌─────────────┐ ┌────────┐ ┌─────────────┐ ┌──────────────┐
│  Narrative   │ │ Module │ │  Context    │ │ Agent        │
│  Service     │ │ System │ │  Runtime    │ │ Framework    │
│ (storylines) │ │ (8 mod)│ │ (prompt     │ │ (LLM SDK     │
│              │ │        │ │  builder)   │ │  adapters)   │
└──────┬───────┘ └───┬────┘ └─────────────┘ └──────────────┘
       │             │
       ▼             ▼
┌─────────────┐ ┌────────────────────────┐
│  MySQL 8    │ │ MCP Servers            │
│  (primary)  │ │ :7801-7805 (per module)│
└─────────────┘ └────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────┐
│  EverMemOS (optional long-term memory)  │
│  MongoDB + Elasticsearch + Milvus       │
└─────────────────────────────────────────┘
```

---

## 3. Service Startup Order

When `bash run.sh` is executed and "Run" is selected, the following 5 services start via tmux:

| Order | Service              | Port(s)    | Role                                          |
|-------|----------------------|------------|-----------------------------------------------|
| 1     | MySQL (Docker)       | 3306       | Primary relational database                   |
| 2     | MCP Servers          | 7801-7805  | Per-module tool servers (via `module_runner.py`)|
| 3     | FastAPI Backend      | 8000       | REST API + WebSocket streaming endpoint       |
| 4     | Job Trigger          | --         | Background daemon polling for scheduled jobs  |
| 5     | Module Poller        | --         | Instance status polling & dependency trigger  |
| 6     | React Frontend       | 5173       | Vite dev server                               |

---

## 4. Agent Runtime -- The 7-Step Execution Pipeline

The `AgentRuntime` class (`src/xyz_agent_context/agent_runtime/agent_runtime.py`) is the **central orchestrator**. Every user message or scheduled job flows through a strict 7-step pipeline. The runtime is an `AsyncGenerator` that yields `ProgressMessage` objects (visible in the UI's Runtime Panel) and `AgentTextDelta` tokens (streamed to the chat).

```
┌─────────────────────────────────────────────────────────────────────┐
│                     AgentRuntime.run() Pipeline                      │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ INITIALIZATION PHASE                                        │    │
│  │   Step 0:   Initialize                                      │    │
│  │             Load agent config, create Event, init Session   │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                              │                                      │
│                              ▼                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ CONTEXT PREPARATION PHASE                                   │    │
│  │   Step 1:   Select Narrative (find/create storyline)        │    │
│  │   Step 1.5: Init Markdown (read historical conversation)    │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                              │                                      │
│                              ▼                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ MODULE LOADING PHASE                                        │    │
│  │   Step 2:   Load Modules (LLM decides which instances)     │    │
│  │   Step 2.5: Sync Instances (persist to DB + markdown)      │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                              │                                      │
│                              ▼                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ EXECUTION PHASE                                             │    │
│  │   Step 3:   Execute Path                                    │    │
│  │             ├─ AGENT_LOOP (99%): LLM reasoning + MCP tools │    │
│  │             └─ DIRECT_TRIGGER (1%): skip LLM, call MCP     │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                              │                                      │
│                              ▼                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ PERSISTENCE PHASE                                           │    │
│  │   Step 4:   Persist Results                                 │    │
│  │             Trajectory file + Event + Narrative summary     │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                              │                                      │
│                              ▼                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ POST-PROCESSING PHASE                                       │    │
│  │   Step 5:   Execute Hooks (each module's after-event hook) │    │
│  │   Step 6:   Process Hook Callbacks (dependency triggers)    │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### Step-by-Step Breakdown

#### Step 0: Initialize

**Purpose**: Set up the execution context for this conversation turn.

| Sub-step | Action | Output |
|----------|--------|--------|
| 0.1 | Load Agent configuration from database | `ctx.agent_data` |
| 0.2 | Initialize `ModuleService` (module loader) | `ctx.module_service` |
| 0.3 | Create an `Event` record (the carrier for this conversation turn) | `ctx.event` |
| 0.4 | Get or create a `Session` (manages continuity across turns) | `ctx.session` |
| 0.5 | Load agent awareness (personality context) | `ctx.awareness` |

#### Step 1: Select Narrative

**Purpose**: Route this user input to the correct semantic storyline.

A **Narrative** is *not* a simple chat thread. It is a semantic topic container that accumulates events, tracks active module instances, and is retrieved by **topic similarity** rather than chronological order.

```
User Input
    │
    ▼
ContinuityDetector: Does this belong to the current Narrative?
  (compares input with session.last_query + current narrative info via LLM)
    │
    ├── YES → Reuse session.current_narrative_id
    │
    └── NO → Vector search across all agent's Narratives
                │
                ├── Score > threshold → Reuse existing Narrative
                │
                └── Score < threshold → Create new Narrative
                                        (generate name, embedding, keywords)
```

**Output**: `ctx.narrative_list` (main + up to K=5 auxiliary narratives), `ctx.main_narrative`

#### Step 1.5: Initialize Markdown

**Purpose**: Read the Markdown file for this Narrative to retrieve historical conversation records and instance state.

Each Narrative has a corresponding `.md` file on disk that records conversation history and instance metadata in a human-readable format. This file serves both as a context source and a debug artifact.

**Output**: `ctx.markdown_history`

#### Step 2: Load Modules (Core Decision Point)

**Purpose**: Use an LLM call with **Structured Output** to decide which Module Instances are needed for this turn.

The LLM receives:
- Current user input
- Narrative's currently active instances
- Narrative summary and history
- Available module metadata (names, descriptions, capabilities)

It returns a structured `InstanceDecisionOutput`:
```json
{
    "should_create_instances": [
        {"module_class": "ChatModule", "instance_id": "chat_abc12345", ...},
        {"module_class": "JobModule", "instance_id": "job_def67890", ...}
    ],
    "should_remove_instances": ["old_instance_xyz"],
    "execution_path": "agent_loop",
    "execution_reasoning": "User asking a question, needs LLM reasoning"
}
```

For each decided instance, the system:
1. Creates a `Module` object (e.g., `ChatModule(agent_id, user_id, db_client)`)
2. Binds the module to the instance (`instance.module = module_object`)
3. Starts the module's MCP Server if required

**Execution Path Decision**:
- **AGENT_LOOP** (~99%): Normal conversation -- LLM reasons and may call MCP tools
- **DIRECT_TRIGGER** (~1%): Explicit API action -- skip LLM, call a specific MCP tool directly

**Output**: `ctx.load_result`, `ctx.active_instances`, `ctx.module_list`

#### Step 2.5: Sync Instances

**Purpose**: Persist instance changes to the database and update the Markdown file.

- Newly created instances are saved to the `module_instances` table and linked to the Narrative via `instance_links`
- Removed instances have their associations deleted
- The Markdown file is updated with the current instance list and a Mermaid relationship graph

#### Step 3: Execute Path (Core Execution Point)

This is where the agent actually "thinks" and responds.

**AGENT_LOOP Path** (the primary path):

```
1. Data Gathering Phase
   └─ For each active Module, call hook_data_gathering(ctx_data)
      ├─ ChatModule:          loads chat history into ctx_data.chat_history
      ├─ SocialNetworkModule: loads entity context into ctx_data
      ├─ JobModule:           loads active jobs into ctx_data
      ├─ MemoryModule:        loads EverMemOS memories into ctx_data
      └─ ... (each module enriches ctx_data with its own data)

2. Context Merging Phase (ContextRuntime)
   └─ Merge all module Instructions (sorted by priority)
   └─ Collect all module MCP Server URLs
   └─ Build the complete System Prompt:
      ┌────────────────────────────────────────┐
      │ System Prompt =                         │
      │   Narrative Info (topic, summary)       │
      │ + Auxiliary Narrative summaries          │
      │ + Module Instructions (priority-sorted) │
      │ + Short-term Memory (cross-narrative)   │
      └────────────────────────────────────────┘

3. Build Messages Array for LLM
   └─ [system prompt] + [long-term chat history] + [current user input]

4. Call Claude Agent SDK
   └─ Connect MCP Servers (tool endpoints)
   └─ Multi-turn reasoning loop (LLM decides when to call tools)
   └─ Stream AgentTextDelta tokens to frontend in real-time

5. Collect Results
   └─ final_output, execution_steps
```

**DIRECT_TRIGGER Path** (rare):
1. Parse the `direct_trigger` config (module_class, tool name, params)
2. Find the target module's MCP Server URL
3. Call the MCP tool directly (no LLM involved)
4. Return the tool result

**Output**: `ctx.execution_result` (final_output, execution_steps, agent_loop_response)

#### Step 4: Persist Results

**Purpose**: Save all execution artifacts.

| Sub-step | Action |
|----------|--------|
| 4.1 | Record **Trajectory** (execution trace file on disk) |
| 4.2 | Update Markdown with new statistics |
| 4.3 | Update the **Event** record: set `final_output`, `event_log`, `module_instances` |
| 4.4 | Update the **Narrative**: append `event_id`, regenerate `dynamic_summary` (LLM-generated per-event summary) |
| 4.5 | Update Session: set `last_query` for next-turn continuity detection |

#### Step 5: Execute Hooks

**Purpose**: Each module's post-processing logic runs here.

```
For each active Module:
    call module.hook_after_event_execution(params)
    │
    ├─ SocialNetworkModule: extract entity info from conversation,
    │                        update entity graph
    ├─ MemoryModule:        write conversation to EverMemOS for
    │                        long-term episodic memory
    ├─ JobModule:           LLM analyzes results, updates job status
    └─ ChatModule:          persist messages to chat history table
```

Each hook can return **callback requests** -- instances that should be triggered next because their dependencies are now satisfied.

**Output**: `hook_callback_results`

#### Step 6: Process Hook Callbacks

**Purpose**: Handle dependency-driven instance activation.

```
For each callback request:
    1. Get the target instance's dependencies
    2. Check: are ALL dependencies completed?
       ├── YES → Spawn a background AgentRuntime.run()
       │         with working_source=CALLBACK
       │         (async, non-blocking)
       └── NO  → Skip (wait for other dependencies)
```

This enables **chained execution**: Job A completes, which unblocks Job B, which then runs automatically.

---

## 5. Module System -- Pluggable, Zero-Coupled Capabilities

### Design Principles

1. **No module imports another module** -- absolute zero coupling
2. **Private packages** for internal implementations (`_module_impl/`)
3. **Centralized registration** via `MODULE_MAP` dictionary
4. **Adding a new module requires zero changes to existing modules**
5. **Each module owns**: its DB tables, MCP server, instructions, data gathering hooks

### Module Base Class: `XYZBaseModule`

Every module extends this abstract base class:

```python
class XYZBaseModule(ABC):
    # Configuration
    def get_config() -> ModuleConfig               # Module metadata (name, priority, ...)

    # Lifecycle Hooks
    async def hook_data_gathering(ctx_data)         # Load data into context (Step 3)
    async def hook_after_event_execution(params)    # Post-processing (Step 5)

    # Instructions
    async def get_instructions(ctx_data) -> str     # System prompt contribution

    # MCP Server
    async def get_mcp_config() -> MCPServerConfig   # MCP server config (URL, name)
    def create_mcp_server() -> MCPServer            # Create MCP server instance

    # Database
    async def init_database_tables()                # Create module-specific tables
    def get_table_schemas() -> List[str]            # SQL CREATE TABLE statements

    # Instance Management
    def get_instance_object_candidates(**kwargs)     # Candidate instances
    def create_instance_object(**kwargs)             # Create instance
```

### Module Registry

```python
# src/xyz_agent_context/module/__init__.py
MODULE_MAP = {
    "MemoryModule":        MemoryModule,         # Highest priority
    "AwarenessModule":     AwarenessModule,
    "BasicInfoModule":     BasicInfoModule,
    "ChatModule":          ChatModule,
    "SocialNetworkModule": SocialNetworkModule,
    "JobModule":           JobModule,
    "GeminiRAGModule":     GeminiRAGModule,
    "SkillModule":         SkillModule,
}
```

### All 8 Modules at a Glance

| # | Module | Instance Prefix | MCP Port | Has MCP Tools | Description |
|---|--------|----------------|----------|---------------|-------------|
| 1 | **MemoryModule** | -- | -- | No | EverMemOS semantic memory; highest priority, runs first in data gathering |
| 2 | **AwarenessModule** | `aware_` | 7801 | Yes | Agent personality, goals, behavioral guidelines, self-awareness |
| 3 | **BasicInfoModule** | `basic_` | -- | No | Static agent info (name, role, creator identification) |
| 4 | **ChatModule** | `chat_` | 7804 | Yes | Multi-user chat history, inbox system, dual-track memory (long-term + short-term) |
| 5 | **SocialNetworkModule** | `social_` | 7802 | Yes | Entity graph: people, organizations, relationships, expertise, interaction history |
| 6 | **JobModule** | `job_` | 7803 | Yes | Task scheduling (one-shot, cron, periodic, continuous) with dependency DAGs |
| 7 | **GeminiRAGModule** | `rag_` | 7805 | Yes | RAG via Google Gemini File Search |
| 8 | **SkillModule** | -- | -- | No | Three-tier skill management (agent-level, user-level, narrative-level) |

---

## 6. Deep Dive: Memory System

NexusMind has a **four-layer memory architecture**. This is one of the most important differentiators of the framework.

```
┌─────────────────────────────────────────────────────────────────┐
│                    Memory Architecture                           │
├──────────┬──────────────────────────────────────────────────────┤
│ Layer    │ Description                                          │
├──────────┼──────────────────────────────────────────────────────┤
│ Layer 1  │ LONG-TERM MEMORY (EverMemOS)                         │
│          │ Cross-conversation semantic memories                  │
│          │ Storage: MongoDB + Elasticsearch + Milvus + Redis     │
│          │ Retrieved by: topic similarity (embedding search)     │
│          │ Written: after each event (Step 5 hook)               │
│          │ Read: during Narrative selection (Step 1)             │
├──────────┼──────────────────────────────────────────────────────┤
│ Layer 2  │ CHAT HISTORY (Dual-Track)                             │
│          │ Long-term track: full conversation history for the    │
│          │   current Narrative → injected as normal messages     │
│          │ Short-term track: cross-Narrative recent conversations│
│          │   → injected into system prompt                       │
│          │ Storage: MySQL (chat_messages, chat_history tables)   │
│          │ Managed by: ChatModule + EventMemoryModule            │
├──────────┼──────────────────────────────────────────────────────┤
│ Layer 3  │ NARRATIVE MEMORY (Event Sequence)                     │
│          │ Sequence of Events within a Narrative                 │
│          │ Each Event: input, output, modules used, timestamps   │
│          │ Dynamic summary: LLM-generated per-event summary      │
│          │ Storage: MySQL (events table)                         │
├──────────┼──────────────────────────────────────────────────────┤
│ Layer 4  │ SOCIAL MEMORY (Entity Graph)                          │
│          │ People, organizations, relationships, expertise       │
│          │ Communication personas, interaction history            │
│          │ Retrieved by: semantic search on entity descriptions  │
│          │ Updated: after each event (Step 5 hook)               │
│          │ Storage: MySQL (social_network_entities table)        │
└──────────┴──────────────────────────────────────────────────────┘
```

### MemoryModule Lifecycle

```
Step 1 (Narrative Selection):
    ┌─ search_evermemos(query, top_k=10)
    │  Vector search across all stored episodes
    └─ Returns relevant memories → injected as auxiliary narrative content

Step 3 (Data Gathering):
    ┌─ hook_data_gathering(ctx_data)
    │  Injects retrieved semantic memories into ctx_data
    └─ Memories appear in the system prompt as "Related Content"

Step 5 (After Execution Hook):
    ┌─ write_to_evermemos(input_content, final_output, narrative_id)
    │  Stores the conversation for future episodic memory
    └─ EverMemOS handles: boundary detection → episode extraction → embedding
```

### Dual-Track Chat Memory (ChatModule)

```
Long-term Memory:
    Complete conversation history of the CURRENT Narrative
    → Passed as normal user/assistant message pairs
    → Provides deep topic context

Short-term Memory:
    Recent conversations from OTHER Narratives (cross-topic)
    → Injected into system prompt as a summary section
    → Truncated to 200 chars per message
    → Provides broader awareness of recent user activity
```

---

## 7. Deep Dive: Narrative System

### What Is a Narrative?

A Narrative is a **semantic topic thread** -- not a chat room, not a memory container, but a **routing index** that:
- Groups related conversations by topic (via embedding similarity)
- Tracks which Module Instances are active on that topic
- Accumulates Events over time
- Maintains a dynamic summary (updated per event)

### Narrative Data Model

```
Narrative
├── id: string                         (unique identifier)
├── agent_id: string                   (owning agent)
├── narrative_info
│   ├── name: string                   ("Project Planning", "Python Help", ...)
│   ├── description: string
│   └── current_summary: string        (latest summary of the topic)
│
├── active_instances: List[Instance]   (currently active module instances)
├── instance_history_ids: List[str]    (completed/archived instances)
│
├── event_ids: List[str]               (events in chronological order)
├── dynamic_summary: List[Entry]       (per-event LLM-generated summaries)
│
├── routing_embedding: List[float]     (1536D vector for similarity search)
├── topic_keywords: List[str]          (search keywords)
├── topic_hint: string                 (topic description)
│
└── env_variables: Dict[str, Any]      (narrative-specific state)
```

### Narrative Selection Flow

```
Input: user_input (e.g., "Schedule a meeting with John next Tuesday")

Step 1: Continuity Detection
    Compare with session.last_query using LLM
    "Is this about the same topic as the previous message?"
    │
    ├── Same topic → reuse session.current_narrative_id
    │
    └── Different topic → proceed to Step 2

Step 2: Vector Search
    Generate embedding for user_input
    Search all agent's Narratives by cosine similarity
    │
    ├── Best match score > threshold
    │   → Reuse that Narrative
    │
    └── No good match
        → Create new Narrative
           ├── LLM generates: name, description, keywords
           ├── Embedding computed from description
           └── Saved to database

Step 3: Load Auxiliary Narratives
    Top-K (up to 5) similar Narratives loaded as context
    Their summaries appear in the system prompt

Output: main_narrative + auxiliary_narratives
```

---

## 8. Deep Dive: Social Network Module

The Social Network Module maintains an **entity graph** that tracks people, organizations, and their relationships.

### Entity Data Model

```
SocialNetworkEntity
├── entity_id: string
├── agent_id: string
├── name: string                       ("John Smith")
├── type: string                       (PERSON, ORGANIZATION, EVENT, CONCEPT)
├── description: string
├── expertise: List[string]            (["Python", "Machine Learning"])
├── contact_info: Dict[str, str]       (email, phone, etc.)
├── communication_persona: string      ("Direct and technical")
├── related_entities: List[string]     (entity IDs of related people)
├── interaction_count: int
├── last_interaction_date: datetime
├── entity_embedding: List[float]      (for semantic search)
└── keywords: List[string]
```

### MCP Tools

| Tool | Purpose |
|------|---------|
| `extract_entity_info()` | Parse & store entity data from conversation |
| `recall_entity()` | Retrieve a specific entity from the graph |
| `search_social_network()` | Semantic search across all entities |

### Update Flow (Step 5 Hook)

```
After each conversation:
1. SocialNetworkModule.hook_after_event_execution() runs
2. LLM analyzes the conversation for entity mentions
3. For each mentioned entity:
   ├── If new: create entity record
   │   ├── Extract name, type, expertise
   │   ├── Generate embedding
   │   └── Save to social_network_entities table
   │
   └── If existing: update entity
       ├── Merge new expertise/info
       ├── Increment interaction_count
       ├── Update last_interaction_date
       └── Refresh embedding
```

---

## 9. Deep Dive: Job Module

The Job Module provides **task scheduling with dependency chains**.

### Job Types

| Type | Description | Example |
|------|-------------|---------|
| **ONE_SHOT** | Execute once (immediately or at a future time) | "Summarize this document" |
| **CRON** | Repeats on a cron schedule | "Every Monday at 9am, check emails" |
| **PERIODIC** | Repeats at a fixed interval | "Every 6 hours, check server status" |
| **CONTINUOUS** | Repeats without a fixed interval | "Keep monitoring this API endpoint" |

### Job Data Model

```
JobModel
├── id: string
├── agent_id: string
├── user_id: string
├── name: string                       ("Weekly Report Generator")
├── description: string
├── job_type: JobType                  (ONE_SHOT, CRON, PERIODIC, CONTINUOUS)
├── status: JobStatus                  (PENDING, RUNNING, COMPLETED, FAILED)
├── trigger_config: TriggerConfig      (schedule, interval, cron expression)
├── parameters: Dict[str, Any]         (job-specific parameters)
├── execution_results: List[Result]    (historical execution records)
├── last_executed_at: datetime
├── next_run_time: datetime
└── dependencies: List[str]            (job IDs this depends on)
```

### MCP Tools

| Tool | Purpose |
|------|---------|
| `job_create()` | Create a new scheduled job |
| `job_retrieval_semantic()` | Search jobs by semantic query |
| `job_retrieval_by_id()` | Get job by ID |
| `job_retrieval_by_keywords()` | Search jobs by keywords |

### Execution Flow

```
Background: job_trigger.py daemon (polls every 60 seconds)
    │
    ├── Check all jobs: status=PENDING/RUNNING, next_run_time <= now
    │
    └── For each ready job:
        1. AgentRuntime.run(working_source=JOB, job_instance_id=...)
        2. Agent executes the job (full 7-step pipeline)
        3. JobModule.hook_after_event_execution():
           └── LLM analyzes execution result
               ├── Update job status (COMPLETED, FAILED, ...)
               ├── Record execution result
               └── Calculate next_run_time (for recurring jobs)
        4. Check dependencies:
           └── If dependent jobs are now unblocked → queue them
```

---

## 10. Deep Dive: Awareness Module

The Awareness Module defines the agent's **personality, goals, and behavioral guidelines**.

### What It Provides

| Aspect | Description |
|--------|-------------|
| **Identity** | Agent name, role, persona description |
| **Goals** | Current objectives and priorities |
| **Guidelines** | Behavioral rules, tone of voice, constraints |
| **Self-Awareness** | What the agent knows about itself and its capabilities |

### Runtime Integration

- **Step 0**: Awareness content loaded from `instance_awareness` table
- **Step 3 (Data Gathering)**: Awareness injected at the top of the system prompt
- **MCP Tools (port 7801)**: Allow the agent to query/update its own awareness dynamically

---

## 11. Deep Dive: Chat Module

The Chat Module manages **multi-user conversation history** with a **dual-track memory** system.

### Dual-Track Memory Architecture

```
┌──────────────────────────────────────────────────────┐
│                  Chat Module Memory                   │
├─────────────────────────┬────────────────────────────┤
│    LONG-TERM TRACK      │    SHORT-TERM TRACK         │
├─────────────────────────┼────────────────────────────┤
│ Full conversation       │ Recent conversations        │
│ history for the         │ from OTHER Narratives       │
│ CURRENT Narrative       │ (cross-topic context)       │
│                         │                             │
│ Injected as:            │ Injected as:                │
│ Normal user/assistant   │ Summary section in          │
│ message pairs           │ system prompt               │
│                         │                             │
│ No truncation           │ Truncated to 200 chars/msg  │
│ (Claude SDK manages     │ Grouped by instance,        │
│  context budget)        │ with relative timestamps    │
└─────────────────────────┴────────────────────────────┘
```

### MCP Tools (port 7804)

The Chat Module also provides inbox functionality, allowing users to receive messages from the agent and from other agents.

---

## 12. Deep Dive: GeminiRAG Module

The GeminiRAG Module provides **document-based retrieval-augmented generation** using Google Gemini's File Search API.

### How It Works

1. **Upload**: Documents are uploaded and indexed via the Gemini API
2. **Query**: When the agent needs information from uploaded documents, it uses the RAG tool
3. **Retrieve**: Gemini File Search returns relevant passages
4. **Generate**: The agent incorporates the retrieved passages into its response

### MCP Tools (port 7805)

Provides tools for document upload, search, and management through the Gemini File Search API.

---

## 13. Deep Dive: Skill Module

The Skill Module manages a **three-tier skill system** for organizing agent capabilities.

| Tier | Scope | Example |
|------|-------|---------|
| **Agent-level** | Available to all narratives for this agent | "Summarize text", "Translate" |
| **User-level** | Available only when interacting with a specific user | Custom preferences |
| **Narrative-level** | Available only within a specific narrative/topic | Topic-specific tools |

The Skill Module does not have its own MCP server -- it contributes to the system prompt by listing available skills.

---

## 14. Instance System -- Runtime Bindings

### What Is a Module Instance?

A **Module Instance** is the runtime binding of a Module within a Narrative. Think of a Module as a "department" (e.g., HR, Engineering) and an Instance as a specific "assignment" from that department to a project (Narrative).

```
Module (class-level capability)
    │
    └── Instance (runtime binding to a Narrative)
        ├── instance_id: "{prefix}_{uuid8}" (e.g., "chat_a1b2c3d4")
        ├── module_class: "ChatModule"
        ├── status: ACTIVE | IN_PROGRESS | BLOCKED | COMPLETED | FAILED | CANCELLED | ARCHIVED
        ├── config: Dict (instance-specific configuration)
        ├── dependencies: List[str] (other instance IDs this depends on)
        └── module: XYZBaseModule (bound Module object, set at runtime)
```

### Instance Creation Patterns

| Pattern | Created When | Instances Per Agent | Example |
|---------|-------------|--------------------:|---------|
| **Agent-level** | On agent creation | 1 | AwarenessModule, BasicInfoModule |
| **Narrative-level** | When user starts chatting in a narrative | 1 per user per narrative | ChatModule for user_alice |
| **Task-level** | Per job creation | Many per narrative | JobModule (each job = 1 instance) |

### Instance Lifecycle

```
Step 2:  LLM decides instance is needed → CREATE (status=ACTIVE)
Step 2.5: Persisted to database, linked to Narrative
Step 3:  Module executes (status may update)
Step 5:  Hooks run, may produce callback requests
Step 6:  Dependencies satisfied → spawn background execution
...
Eventually: status → COMPLETED or ARCHIVED
```

---

## 15. Context Runtime -- The Prompt Builder

The `ContextRuntime` class (`src/xyz_agent_context/context_runtime/context_runtime.py`) is responsible for assembling the final prompt that goes to the LLM. It runs inside Step 3.

### Assembly Process

```
Step 0: Initialize ContextData
    └─ agent_id, user_id, input_content, narrative_id, working_source

Step 1: Extract Narrative data
    └─ Load topic info, auxiliary narrative summaries

Step 2: Gather data from all Module instances
    └─ Call hook_data_gathering() on each module
       ├─ ctx_data.chat_history       ← ChatModule
       ├─ ctx_data.awareness          ← AwarenessModule
       ├─ ctx_data.extra_data["..."]  ← SocialNetworkModule, JobModule, etc.
       └─ ctx_data.semantic_memories  ← MemoryModule

Step 3: Build Module instructions (deduplicated by module_class)
    └─ Call get_instructions(ctx_data) on each unique module
    └─ Sort by priority

Step 4: Build complete System Prompt
    ┌──────────────────────────────────┐
    │  Part 1: Narrative Info          │  (topic, summary)
    │  Part 2: Auxiliary Narratives    │  (related topic summaries + EverMemOS content)
    │  Part 3: Module Instructions     │  (priority-sorted, from all active modules)
    │  Part 4: Short-term Memory       │  (cross-narrative recent conversations)
    └──────────────────────────────────┘

Step 5: Build messages array
    [
        { role: "system",    content: <complete system prompt> },
        { role: "user",      content: <historical msg 1> },
        { role: "assistant", content: <historical msg 2> },
        ...                           (long-term chat history)
        { role: "user",      content: <current user input> }
    ]

    MCP URLs: { "chat_module": "http://127.0.0.1:7804/sse", ... }
```

### Message Truncation

- **Single message cap**: 4000 characters per message (prevents one large paste from consuming the context budget)
- **Overall budget**: Managed by Claude Agent SDK's `MAX_HISTORY_LENGTH`
- **Short-term memory**: 200 chars per message in cross-narrative summaries

---

## 16. Agent Framework -- LLM Adapters

NexusMind supports multiple LLM providers through a unified adapter layer.

| Adapter | File | Primary Use | Protocol |
|---------|------|-------------|----------|
| **ClaudeAgentSDK** | `xyz_claude_agent_sdk.py` | Core agent reasoning + tool calling | Claude Code SDK, streaming, multi-turn |
| **OpenAIAgentsSDK** | `openai_agents_sdk.py` | Embeddings, entity analysis, summaries | OpenAI API |
| **GeminiAPI** | `gemini_api_sdk.py` | RAG File Search | Google Gemini API |

### Claude Agent SDK Integration (Primary)

The Claude Agent SDK is the primary execution engine. During Step 3 (Agent Loop), the system:
1. Passes the complete system prompt + message history
2. Connects MCP Servers as tool endpoints
3. Claude reasons about the query and decides when to call tools
4. Token output is streamed in real-time via `AgentTextDelta`
5. Multi-turn tool calling is supported (Claude can call tools, observe results, and reason further)

---

## 17. Data Flow Summary -- End to End

```
User types "Schedule a weekly report for John every Monday"
                    │
                    ▼
[Frontend] WebSocket message sent to backend
                    │
                    ▼
[Step 0] Create Event, load Agent config, init Session
                    │
                    ▼
[Step 1] ContinuityDetector: new topic detected
         Vector search: no matching Narrative
         → Create new Narrative "Weekly Report Scheduling"
                    │
                    ▼
[Step 1.5] Initialize Markdown file for new Narrative
                    │
                    ▼
[Step 2] LLM decides:
         ├─ ChatModule instance (for conversation)
         ├─ JobModule instance (scheduling detected)
         ├─ SocialNetworkModule instance ("John" mentioned)
         └─ execution_path: AGENT_LOOP
                    │
                    ▼
[Step 2.5] Persist 3 new instances to database
                    │
                    ▼
[Step 3] Data Gathering:
         ├─ ChatModule: no prior history (new narrative)
         ├─ JobModule: no existing jobs
         └─ SocialNetworkModule: look up "John" in entity graph

         Context Building:
         └─ System prompt + message array built

         Agent Loop (Claude):
         ├─ Reasons about the request
         ├─ Calls JobModule.job_create() via MCP
         │   └─ Creates CRON job: "Weekly Report", schedule: "0 9 * * 1"
         ├─ Calls SocialNetworkModule.recall_entity("John") via MCP
         │   └─ Retrieves John's info
         └─ Generates response: "I've created a weekly report job..."
                    │
                    ▼
[Step 4] Persist:
         ├─ Event saved with final_output
         ├─ Narrative updated with event_id + dynamic_summary
         └─ Trajectory file written
                    │
                    ▼
[Step 5] Hooks:
         ├─ SocialNetworkModule: update John's interaction_count
         ├─ MemoryModule: write to EverMemOS
         └─ JobModule: analyze results, confirm job status
                    │
                    ▼
[Step 6] No callback triggers needed
                    │
                    ▼
[Frontend] User sees streamed response + Runtime Panel shows all steps
```

---

## 18. Key Design Patterns

| Pattern | Where Used | Purpose |
|---------|-----------|---------|
| **Module Independence** | Module system | Zero coupling between modules; private packages |
| **Hook-Based Integration** | Module lifecycle | `hook_data_gathering` + `hook_after_event_execution` |
| **Repository Pattern** | Data access layer | Type-safe CRUD; batch loading solves N+1 problem |
| **Structured Output** | Step 2 (instance decisions) | LLM returns guaranteed JSON schema |
| **Async-First** | Entire codebase | `AsyncDatabaseClient`, `AsyncGenerator`, `asyncio` |
| **Dependency Injection** | AgentRuntime | Optional services for testing/customization |
| **Semantic Routing** | Narrative selection | Embedding similarity, not keyword matching |
| **Dual-Track Memory** | ChatModule | Long-term (full history) + Short-term (cross-narrative) |

---

## 19. Critical File Locations

| File / Directory | Purpose |
|------------------|---------|
| `src/xyz_agent_context/agent_runtime/agent_runtime.py` | Main 7-step orchestrator |
| `src/xyz_agent_context/agent_runtime/_agent_runtime_steps/` | Individual step implementations |
| `src/xyz_agent_context/module/base.py` | `XYZBaseModule` base class |
| `src/xyz_agent_context/module/__init__.py` | `MODULE_MAP` registration |
| `src/xyz_agent_context/module/memory_module/` | EverMemOS integration |
| `src/xyz_agent_context/module/chat_module/` | Chat history + dual-track memory |
| `src/xyz_agent_context/module/social_network_module/` | Entity graph |
| `src/xyz_agent_context/module/job_module/` | Job scheduling + dependency DAGs |
| `src/xyz_agent_context/module/awareness_module/` | Agent personality/goals |
| `src/xyz_agent_context/module/gemini_rag_module/` | RAG via Gemini File Search |
| `src/xyz_agent_context/module/skill_module/` | Three-tier skill management |
| `src/xyz_agent_context/module/basic_info_module/` | Static agent info |
| `src/xyz_agent_context/narrative/narrative_service.py` | Narrative CRUD + selection |
| `src/xyz_agent_context/narrative/event_service.py` | Event CRUD |
| `src/xyz_agent_context/context_runtime/context_runtime.py` | System prompt builder |
| `src/xyz_agent_context/agent_framework/` | LLM SDK adapters |
| `src/xyz_agent_context/repository/base.py` | Generic repository pattern |
| `src/xyz_agent_context/schema/` | All Pydantic models (centralized) |
| `backend/main.py` | FastAPI entry point |
| `backend/routes/websocket.py` | WebSocket streaming |
| `frontend/src/` | React 19 UI components |
| `run.sh` | Unified deployment entry |
| `docker-compose.yaml` | MySQL Docker setup |
| `.evermemos/` | EverMemOS (git submodule) |

---

## 20. Summary: What Makes NexusMind Different

| Dimension | Traditional Agent Frameworks | NexusMind |
|-----------|------------------------------|-----------|
| **Focus** | Making one agent smarter | Making agents connected |
| **Memory** | Session-level, forgotten between conversations | Multi-layered: EverMemOS (long-term) + Chat (dual-track) + Narrative (event sequence) + Social (entity graph) |
| **Organization** | Flat conversation threads | Semantic Narratives with routing embeddings |
| **Capabilities** | Fixed toolset | Hot-swappable modules with zero coupling |
| **Tasks** | One-off function calls | Dependency-aware job DAGs with cron/periodic/continuous scheduling |
| **Social** | No social awareness | Entity graph with relationships, expertise, communication personas |
| **Transparency** | Black box | Every step visible in real-time (7-step pipeline + Runtime Panel) |
| **Scalability** | Single user | Multi-user isolation (per agent_id + user_id) |
