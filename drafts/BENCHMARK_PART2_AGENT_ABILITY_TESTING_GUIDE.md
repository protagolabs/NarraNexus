# NexusMind -- Agent Ability Benchmark Testing Guide

**Part 2: Benchmarking Agent Capabilities (GAIA, tau-bench, BFCL, and Others)**

---

## 1. Purpose of This Document

This guide helps researchers and interns understand:
1. What **standard agent benchmarks** exist and what they measure
2. What **capabilities NexusMind currently has** (and does not have)
3. How to **map benchmark tasks to NexusMind's modules and tools**
4. **Practical step-by-step instructions** for running benchmark-style tests -- where to insert files, how to configure the agent, and what to observe

---

## 2. NexusMind Agent Capability Inventory

Before discussing benchmarks, here is the complete inventory of tools and capabilities the NexusMind agent can use at runtime.

### 2.1 MCP Tools (Callable by the Agent via LLM)

| Module | Port | Tool | Description |
|--------|------|------|-------------|
| **ChatModule** | 7804 | `send_message_to_user_directly` | Deliver a response to the user (the ONLY way to "speak") |
| | | `agent_send_content_to_user_inbox` | Send async notification to user's inbox |
| | | `agent_send_content_to_agent_inbox` | Inter-agent messaging |
| | | `get_inbox_status` | Query unread message counts |
| | | `get_chat_history` | Retrieve conversation history for a chat instance |
| **SocialNetworkModule** | 7802 | `extract_entity_info` | Parse & store entity data (name, expertise, tags) |
| | | `get_contact_info` | Retrieve stored contact information |
| | | `search_social_network` | Find entities via exact ID, tag, semantic, or name search |
| | | `get_agent_social_stats` | View network statistics (sorted by recency/frequency/strength) |
| **JobModule** | 7803 | `job_create` | Create scheduled tasks (ONE_OFF, SCHEDULED, ONGOING, RECURRING) |
| | | `job_retrieval_semantic` | Search jobs using semantic similarity |
| | | `job_retrieval_by_id` | Get job by ID |
| | | `job_retrieval_by_keywords` | Keyword-based job search |
| | | `job_update` | Update job properties, scheduling, status |
| **AwarenessModule** | 7801 | `update_awareness` | Update agent's self-awareness profile |
| **GeminiRAGModule** | 7805 | `rag_query` | Search uploaded documents via natural language |
| | | `rag_upload_file` | Upload files to the RAG knowledge base |
| | | `rag_upload_text` | Upload text content directly to the knowledge base |

### 2.2 Non-MCP Capabilities (Automatic / Hook-Based)

| Capability | Module | How It Works |
|-----------|--------|-------------|
| Long-term semantic memory | MemoryModule | Automatic read in Step 1 (Narrative selection), automatic write in Step 5 (hook) via EverMemOS |
| Dual-track chat history | ChatModule | Long-term (current narrative) + Short-term (cross-narrative recent messages) |
| Entity graph updates | SocialNetworkModule | Automatic extraction after each conversation (Step 5 hook) |
| Dynamic narrative summaries | NarrativeService | LLM-generated summary updated per event |
| Skill loading | SkillModule | Reads SKILL.md files from agent workspace (filesystem-based, not MCP) |

### 2.3 LLM Execution Engine

| Aspect | Detail |
|--------|--------|
| Primary engine | Claude Agent SDK (Claude Sonnet) |
| System prompt budget | ~60 KB |
| History budget | ~30 KB |
| MCP response buffer | 50 MB |
| Streaming | Token-level via WebSocket |
| Multi-turn tool use | Yes -- Claude can call tools, observe results, reason, call more tools |

### 2.4 Extended Capabilities via Claude Code

NexusMind's Agent execution loop is built on top of **Claude Code**, so all of Claude Code's native tools are directly available to the Agent. Below is the actual availability status of these capabilities:

| Capability | Status | Details |
|------------|--------|---------|
| Web browsing / web search | ✅ Available | Claude Code provides `WebSearch` (web search) and `WebFetch` (web page fetching) tools for answering questions requiring internet lookup |
| Code execution (Python/Shell) | ✅ Available | Claude Code's `Bash` tool can directly execute Python scripts and shell commands, supporting arbitrary computation and data processing |
| Direct file parsing (PDF/Excel/CSV) | ✅ Available | Claude Code's `Read` tool natively supports PDF reading; Excel/CSV can be processed via Bash running Python (pandas, etc.) |
| Image analysis / OCR | ✅ Available | Claude Code's `Read` tool supports multimodal image input (PNG, JPG, etc.) and Claude has built-in visual understanding |
| Calculator / math tool | ✅ Available | Can execute Python via `Bash` for precise mathematical calculations instead of relying on LLM arithmetic approximation |
| General SQL / database query tool | ✅ Available | Can execute `mysql`, `psql`, `sqlite3`, and other database client commands via `Bash` |
| Audio transcription | ⚠️ Extensible | No native audio processing in Claude Code, but can install and invoke `whisper`, `ffmpeg`, etc. via Bash |
| Video / YouTube processing | ⚠️ Extensible | Can install `yt-dlp` via Bash to download video / extract subtitles, combined with `whisper` for transcription |

> **Key implication for benchmarks**: Because Claude Code provides web search, code execution, file reading, and image understanding, NexusMind's capability coverage on benchmarks like GAIA is significantly broader than what MCP tools alone would provide. Researchers should fully leverage these native Claude Code capabilities during testing.

---

## 3. Benchmark Overview

### 3.1 GAIA (General AI Assistants)

**Source**: Meta/HuggingFace, ICLR 2024
**Dataset**: [huggingface.co/datasets/gaia-benchmark/GAIA](https://huggingface.co/datasets/gaia-benchmark/GAIA)
**Leaderboard**: [hal.cs.princeton.edu/gaia](https://hal.cs.princeton.edu/gaia)

**What it tests**: Real-world questions requiring multi-step reasoning, web browsing, file processing, and multimodal understanding. Trivially easy for humans (92%) but very hard for AI (~65% for best systems).

| Level | Questions | Human Steps | Description |
|-------|-----------|-------------|-------------|
| Level 1 | 146 | ~5 steps | Simple questions, 0-1 tools |
| Level 2 | 245 | 5-10 steps | Multiple tools, multi-step planning |
| Level 3 | 75 | Up to 50 steps | Arbitrarily long action sequences |

**File types in dataset**: PDF, Excel/XLSX, Python (.py), PNG/JPG (images), MP3 (audio), YouTube videos

**Capabilities required** (by frequency):

| Capability | % of Questions | NexusMind Status |
|-----------|---------------|------------------|
| Web browsing/search | ~70-80% | **NOT PRESENT** |
| Code execution | ~50-60% | **PARTIAL** (Claude has bash, no sandboxed Python) |
| File reading (PDF, Excel, images) | ~40-50% | **PARTIAL** (RAG upload only, no direct parsing) |
| Multi-step reasoning | 100% | **YES** (7-step pipeline + multi-turn tool calling) |
| Multimodal (image/OCR) | ~20-30% | **NOT PRESENT** |
| Audio transcription | ~10-20% | **NOT PRESENT** |
| YouTube transcript extraction | ~10-20% | **NOT PRESENT** |

**Scoring**: Exact string match on final answer.

### 3.2 tau-bench / tau2-bench

**Source**: Sierra Research, ICLR 2025
**Code**: [github.com/sierra-research/tau-bench](https://github.com/sierra-research/tau-bench)

**What it tests**: Conversational customer service agents -- multi-turn dialogue, policy compliance, correct tool use, and database state modification.

| Domain | Description |
|--------|-------------|
| Airline | Flight booking, modification, cancellation, refunds |
| Retail | Product inquiries, order management, returns/exchanges |
| Telecom (tau2 only) | Service management; dual-control (both agent AND user have tools) |

**Capabilities required**:

| Capability | Importance | NexusMind Status |
|-----------|-----------|------------------|
| Structured function calling | Critical | **YES** (MCP tools) |
| Multi-turn dialogue | Critical | **YES** (ChatModule + narrative memory) |
| Policy/rule adherence | Critical | **PARTIAL** (system prompt instructions; no formal policy engine) |
| State management | Critical | **YES** (instance state, narrative env_variables) |
| Information gathering | High | **YES** (multi-turn conversation) |
| Consistency across runs | High | **PARTIAL** (LLM non-determinism) |

**Scoring**: `pass^k` -- probability of succeeding on ALL of k independent trials. Verified via database state comparison + assertion checks.

### 3.3 BFCL (Berkeley Function-Calling Leaderboard)

**Source**: [gorilla.cs.berkeley.edu/leaderboard.html](https://gorilla.cs.berkeley.edu/leaderboard.html)

**What it tests**: Pure function/tool calling accuracy -- generating correct function calls with correct arguments.

| Category | Description |
|----------|-------------|
| Simple calls | Single function, correct arguments |
| Multiple calls | Multiple functions in sequence |
| Parallel calls | Multiple independent calls simultaneously |
| Irrelevance detection | Knowing when NOT to call any tool |
| Multi-turn | Multi-step function call chains |

**NexusMind relevance**: Directly testable via MCP tools. The LLM's ability to select the right tool and provide correct arguments can be measured.

### 3.4 Other Relevant Benchmarks

| Benchmark | Focus | NexusMind Fit |
|-----------|-------|--------------|
| **AgentBench** | 8 environments (OS, DB, web, games) | Low -- requires web browsing, OS interaction |
| **SWE-bench** | Resolve real GitHub issues | Low -- requires code editing, testing |
| **WebArena** | Web task completion | Low -- requires web browsing |
| **ToolBench** | Large-scale API tool use (16K+ APIs) | Medium -- MCP tools provide structured calling |
| **MINT** | Multi-turn with tools + human feedback | Medium -- multi-turn is native |

---

## 4. Benchmark-to-NexusMind Capability Mapping

### 4.1 What NexusMind Can Test Well (Native Strengths)

| Benchmark Category | How to Test in NexusMind |
|-------------------|--------------------------|
| **RAG / Knowledge Retrieval** | Upload documents via `rag_upload_file`, query via `rag_query` |
| **Multi-turn Conversation** | Use WebSocket chat, measure across turns |
| **Tool Selection & Calling** | Present tasks requiring specific MCP tools, verify correct tool usage |
| **Entity/Relationship Management** | Test social network extraction and recall accuracy |
| **Task Scheduling & Dependencies** | Create jobs with dependency chains, verify correct execution order |
| **Memory Recall** | Test cross-conversation memory persistence via EverMemOS |
| **Policy Adherence** | Inject domain rules via Awareness Module, test compliance |

### 4.2 What Requires Additional Setup (Gaps to Fill)

| Benchmark Requirement | Gap | Recommended Solution |
|----------------------|-----|---------------------|
| **Web search** | No web tool | Add a web search MCP server (e.g., Tavily, SerpAPI, Brave Search) |
| **Code execution** | No sandboxed Python | Add a code execution MCP server (e.g., E2B sandbox, Docker-based executor) |
| **PDF text extraction** | RAG indexes but doesn't return raw text | Add a file-parsing MCP server (e.g., PyMuPDF, pdfplumber) |
| **Excel/CSV reading** | Not supported | Add a spreadsheet-parsing MCP server (e.g., openpyxl, pandas) |
| **Image analysis** | No vision/OCR tool | Add an image analysis MCP server (e.g., GPT-4o vision, Tesseract OCR) |
| **Audio transcription** | Not supported | Add an audio MCP server (e.g., Whisper API) |
| **Calculator** | No dedicated tool | Add a calculator MCP server or rely on code execution |

### 4.3 Capability Matrix Summary

```
                          GAIA    tau-bench   BFCL    Custom RAG   Custom Memory
                          ─────   ─────────   ────    ──────────   ─────────────
Web Search                 ✗✗✗       -          -         -             -
Code Execution             ✗✗        -          -         -             -
File Parsing (PDF/Excel)   ✗✗        -          -        ✗✗             -
Multimodal (Image/Audio)   ✗✗        -          -         -             -
RAG Query                   ✓        -          -        ✓✓✓            -
Multi-turn Dialogue         ✓       ✓✓✓        ✓✓        ✓            ✓✓
Tool Selection/Calling      ✓       ✓✓✓       ✓✓✓        ✓             ✓
Policy Adherence            -       ✓✓✓         -         -             -
Memory Persistence          -         -          -         -           ✓✓✓
Entity Graph                -         -          -         -           ✓✓✓
Job Scheduling              -         -          -         -           ✓✓✓

Legend: ✓✓✓ = Native strength   ✓ = Supported   ✗✗ = Gap (needs additional MCP)
        ✗✗✗ = Critical gap      - = Not relevant for this benchmark
```

---

## 5. Practical Testing Guidelines

### 5.1 Testing RAG / Knowledge Retrieval (GAIA-style File Questions)

#### What You're Testing
The agent's ability to ingest documents, index them, and answer questions based on their content.

#### Step-by-Step Setup

**1. Prepare benchmark files**

Collect the document files from GAIA's dataset (PDFs, text files, etc.). Place them in a staging directory:
```
/path/to/benchmark_files/
├── question_001.pdf
├── question_002.txt
├── question_003.xlsx    # ⚠ Not natively supported -- see note below
└── ...
```

**2. Upload files to the RAG knowledge base**

Option A -- Via the agent conversation (tests the agent's autonomous upload ability):
```
User: "I've placed a document at /path/to/benchmark_files/question_001.pdf.
       Please upload it to your knowledge base and tell me what it contains."
```
The agent should call `rag_upload_file(agent_id, file_path="/path/to/benchmark_files/question_001.pdf")`.

Option B -- Via the REST API (pre-load files before testing):
```bash
# Upload file to agent workspace first
curl -X POST http://localhost:8000/api/agents/{agent_id}/files \
  -F "file=@question_001.pdf"

# Then have the agent upload to RAG via conversation
# or call the MCP tool directly via DIRECT_TRIGGER
```

Option C -- Via `rag_upload_text` for text content:
```
User: "Add this information to your knowledge base:
       [paste text content here]"
```

**3. Ask benchmark questions**

```
User: "Based on the document I uploaded, what is the total revenue
       reported in Q3?"
```
The agent should call `rag_query(agent_id, query="total revenue Q3")` and return the answer.

**4. Evaluate**

Compare the agent's final answer against the ground truth using exact string match (GAIA scoring).

#### Important Notes on File Types

| File Type | RAG Support | What to Do |
|-----------|------------|------------|
| PDF | **Yes** | Upload directly via `rag_upload_file` |
| TXT, MD | **Yes** | Upload directly via `rag_upload_file` or `rag_upload_text` |
| DOCX | **Yes** | Upload directly via `rag_upload_file` |
| **Excel/XLSX** | **No** | Convert to CSV/TXT first, then upload. Or add a spreadsheet-parsing MCP server |
| **Images (PNG/JPG)** | **No** | Embed in PDF first, or add a vision MCP server |
| **Audio (MP3)** | **No** | Transcribe first (Whisper), then upload transcript |
| **Python (.py)** | **Yes** (as text) | Upload as text file |

#### Where Files Go

```
Upload path:        User uploads → backend saves to agent workspace
Agent workspace:    {base_working_path}/{agent_id}_{user_id}/
RAG temp dir:       ./data/gemini_rag_temp/
RAG store mapping:  ./data/gemini_file_search_map.json
Gemini backend:     Files indexed in Google Gemini File Search (cloud)
```

#### RAG Testing Checklist

- [ ] Verify Google Gemini API key is configured in `.env` (`GOOGLE_API_KEY`)
- [ ] Verify GeminiRAGModule MCP server is running on port 7805
- [ ] Upload test documents before asking questions
- [ ] Allow time for indexing (Gemini File Search may take a few seconds)
- [ ] Check `./data/gemini_file_search_map.json` to verify agent-to-store mapping
- [ ] Test with queries of varying specificity (exact phrase vs. semantic)

---

### 5.2 Testing Tool Use / Function Calling (BFCL-style / tau-bench-style)

#### What You're Testing
The agent's ability to select the correct MCP tool and provide the correct arguments.

#### Test Design

Create test scenarios that require specific tools:

**A. Single tool call tests**
```
User: "Create a one-time job called 'Generate Report' that runs tomorrow at 9am."
Expected: agent calls job_create(agent_id, user_id, title="Generate Report",
          job_type="ONE_OFF", trigger_config={...})
```

**B. Multi-tool tests**
```
User: "Look up John Smith in my contacts, then schedule a weekly meeting
       reminder with him every Monday."
Expected:
  1. search_social_network(agent_id, search_keyword="John Smith", search_type="name")
  2. job_create(agent_id, ..., job_type="SCHEDULED", trigger_config={cron: "0 9 * * 1"})
```

**C. Irrelevance detection tests**
```
User: "What is the capital of France?"
Expected: No tool calls (pure LLM reasoning), direct response via send_message_to_user_directly
```

**D. Policy adherence tests (tau-bench-style)**
Inject domain rules via the Awareness Module, then test compliance:
```
# Pre-configure awareness with business rules:
"POLICY: Refunds are only allowed within 30 days of purchase.
 POLICY: Orders over $500 require manager approval.
 POLICY: Never disclose customer payment details."

User: "I want a refund for order #12345, purchased 45 days ago."
Expected: Agent should deny refund based on 30-day policy.
```

#### How to Observe Tool Calls

1. **Runtime Panel** (frontend): Shows each step of the 7-step pipeline, including which tools were called
2. **Trajectory files**: Saved to `./data/trajectories/{agent_id}_{user_id}/` after each event
3. **Event records**: In MySQL `events` table, the `event_log` field records all execution steps
4. **Agent logs**: Check tmux window or log files for detailed MCP call traces

#### Tool Calling Evaluation Metrics

| Metric | How to Measure |
|--------|---------------|
| **Tool selection accuracy** | Did the agent call the correct tool(s)? |
| **Argument correctness** | Were the arguments correct (types, values)? |
| **Call ordering** | Were multi-step calls in the correct order? |
| **Irrelevance detection** | Did the agent correctly avoid tool calls when none were needed? |
| **Completeness** | Did the agent call ALL required tools? |

---

### 5.3 Testing Multi-turn Conversation (tau-bench-style)

#### What You're Testing
The agent's ability to maintain context across multiple conversation turns, gather information incrementally, and complete multi-step tasks.

#### Test Design

Create multi-turn scenarios with specific information gathering requirements:

```
Turn 1 - User: "I want to book a flight."
Expected: Agent asks for details (origin, destination, date)

Turn 2 - User: "From New York to San Francisco."
Expected: Agent asks for date and preferences

Turn 3 - User: "Next Friday, economy class."
Expected: Agent creates a job or takes action with all gathered info

Turn 4 - User: "Actually, change that to business class."
Expected: Agent updates without re-asking for other details
```

#### How to Run Multi-turn Tests

1. Connect via WebSocket to `ws://localhost:8000/ws?agent_id={agent_id}&user_id={user_id}`
2. Send messages sequentially, waiting for each response
3. Record all turns (both user and agent messages)
4. Verify state at each turn (check what the agent remembered, what tools it called)

#### What to Observe

| Observation Point | How to Check |
|-------------------|-------------|
| Context retention | Does the agent remember info from Turn 1 in Turn 4? |
| Chat history accuracy | Check `get_chat_history` via MCP or MySQL `chat_messages` table |
| Narrative continuity | Does the agent stay in the same Narrative across turns? |
| Session state | Check `sessions` table for `last_query` and `current_narrative_id` |

---

### 5.4 Testing Job Scheduling & Dependencies

#### What You're Testing
The agent's ability to create, schedule, and manage tasks with dependency chains.

#### Test Scenarios

**A. Simple one-shot job**
```
User: "Remind me to call John tomorrow at 3pm."
Expected: job_create with job_type=ONE_OFF, trigger_config with specific time
```

**B. Recurring job**
```
User: "Every Monday morning, generate a summary of last week's conversations."
Expected: job_create with job_type=SCHEDULED, cron expression "0 9 * * 1"
```

**C. Job with dependencies**
```
User: "First, research the competitor's pricing. Then, draft a comparison report.
       Finally, send it to my inbox."
Expected:
  Job 1: "Research pricing" (no dependencies)
  Job 2: "Draft comparison" (depends_on: [Job 1])
  Job 3: "Send report" (depends_on: [Job 2])
```

**D. Job status management**
```
User: "What jobs do I have pending?"
Expected: job_retrieval_semantic or job_retrieval_by_keywords
```

#### Verification

- Check MySQL `instance_jobs` table for job records
- Verify `job_trigger.py` daemon picks up and executes jobs at the right time
- Check dependency chain execution order in trajectory files
- Verify `next_run_time` is correctly calculated for recurring jobs

---

### 5.5 Testing Social Network / Entity Graph

#### What You're Testing
The agent's ability to extract, store, and recall entity information from conversations.

#### Test Scenarios

**A. Entity extraction (automatic, via Step 5 hook)**
```
Turn 1 - User: "I just had a meeting with Dr. Sarah Chen. She's a machine learning
               expert at Stanford who specializes in NLP."
[Wait for Step 5 hook to run]

Turn 2 - User: "What do you know about Sarah Chen?"
Expected: Agent recalls entity via search_social_network, returns expertise, affiliation
```

**B. Multi-entity relationship test**
```
User: "Bob and Alice are co-founders of TechCorp. Bob handles engineering
       and Alice handles marketing."
[Wait for hook]

User: "Who works at TechCorp?"
Expected: Agent finds both Bob and Alice via search_social_network
```

**C. Entity update test**
```
Turn 1: "John is a Python developer."
Turn 2: [Multiple conversations later] "John just got promoted to CTO."
Turn 3: "What's John's current role?"
Expected: Agent returns updated info (CTO), not stale info (developer)
```

#### Verification

- Check MySQL `instance_social_entities` table for entity records
- Verify embeddings are generated (non-null `entity_embedding`)
- Test semantic search quality (can it find "ML expert" when querying "artificial intelligence researcher"?)

---

### 5.6 Testing Awareness / Self-Identity

#### What You're Testing
The agent's ability to maintain and act according to its configured personality and behavioral guidelines.

#### Test Design

**1. Configure awareness profile**

Via REST API:
```bash
curl -X PUT http://localhost:8000/api/agents/{agent_id}/awareness \
  -H "Content-Type: application/json" \
  -d '{"awareness": "You are a formal business consultant. Always use professional language. Never use slang or emoji. Address users as Mr./Ms. followed by their last name."}'
```

Or via conversation:
```
User: "Update your awareness: You are a formal business consultant..."
```

**2. Test compliance**
```
User: "hey whats up dude"
Expected: Agent responds formally despite casual input
```

**3. Test persistence**
Restart the conversation (new session). The awareness should persist from the database.

---

## 6. Recommended Benchmark Testing Plan

### Phase 1: Native Capability Benchmarks (No Additional Setup)

| Test Category | Benchmark Style | # Test Cases | Modules Tested |
|--------------|----------------|-------------|----------------|
| RAG accuracy | GAIA (file questions) | 20-50 | GeminiRAGModule |
| Tool selection | BFCL-style | 30-50 | All MCP modules |
| Multi-turn dialogue | tau-bench-style | 10-20 | ChatModule, all |
| Entity extraction | Custom | 15-20 | SocialNetworkModule |
| Job scheduling | Custom | 10-15 | JobModule |
| Policy adherence | tau-bench-style | 10-15 | AwarenessModule |
| Memory persistence | Custom | 10-15 | MemoryModule, ChatModule |

### Phase 2: Extended Benchmarks (Requires Additional MCP Servers)

| Test Category | Additional MCP Needed | Benchmark |
|--------------|----------------------|-----------|
| Web search questions | Web search server (Tavily/SerpAPI) | GAIA Level 1-2 |
| Code execution tasks | Python sandbox server (E2B) | GAIA Level 2-3 |
| File parsing tasks | PDF/Excel parser server | GAIA (file questions) |
| Image understanding | Vision server (GPT-4o vision) | GAIA (multimodal) |
| Full GAIA evaluation | All of the above | GAIA full benchmark |
| Full tau-bench | Domain API servers (airline/retail) | tau-bench |

### Phase 3: Full Benchmark Runs

Once additional MCP servers are in place, run the full benchmark suites:

```bash
# GAIA: Load dataset, run questions, compare answers
python gaia_runner.py --agent-url ws://localhost:8000/ws \
  --agent-id {agent_id} --user-id benchmark_user \
  --dataset gaia-benchmark/GAIA --split test

# tau-bench: Connect to simulated environment
python run.py --agent-strategy tool-calling --env retail \
  --model claude-sonnet --max-concurrency 10
```

---

## 7. Configuration Checklist for Benchmarking

### Environment Setup

```bash
# 1. Verify all services are running
bash run.sh  # Select "Status" to check

# 2. Verify API keys in .env
OPENAI_API_KEY=sk-...          # Required for embeddings
GOOGLE_API_KEY=AI...            # Required for Gemini RAG
# Claude Code CLI must be authenticated

# 3. Verify MCP servers are responding
curl http://localhost:7801/health  # Awareness
curl http://localhost:7802/health  # Social Network
curl http://localhost:7803/health  # Job
curl http://localhost:7804/health  # Chat
curl http://localhost:7805/health  # Gemini RAG

# 4. Create a benchmark agent
# Via frontend: http://localhost:5173 → Create Agent

# 5. Upload benchmark files (for RAG tests)
curl -X POST http://localhost:8000/api/agents/{agent_id}/files \
  -F "file=@benchmark_document.pdf"
```

### Benchmark Agent Configuration

For fair benchmarking, configure the agent with minimal personality to reduce interference:

```json
{
  "awareness": "You are a helpful AI assistant being evaluated on a benchmark test. Answer questions accurately and concisely. Use tools when needed. Provide only the final answer."
}
```

### Logging & Observation

| What to Capture | Where to Find It |
|----------------|-----------------|
| Tool call trace | `./data/trajectories/{agent_id}_{user_id}/{event_id}.json` |
| Full event log | MySQL `events` table → `event_log` column |
| Runtime steps | Frontend Runtime Panel (real-time) |
| Agent response | MySQL `events` table → `final_output` column |
| Narrative selection | Check `narratives` table and trajectory file |
| Instance decisions | Step 2 in trajectory file (InstanceDecisionOutput) |

---

## 8. Evaluation Metrics Reference

| Metric | Formula | Applicable Benchmarks |
|--------|---------|----------------------|
| **Exact Match (EM)** | `answer == ground_truth` | GAIA |
| **pass^k** | `p^k` averaged across tasks | tau-bench |
| **Tool Accuracy** | `correct_tool_calls / total_questions` | BFCL, custom |
| **Argument Accuracy** | `correct_args / total_tool_calls` | BFCL, custom |
| **Recall@K** | `relevant_retrieved / total_relevant` | RAG tests |
| **Precision@K** | `relevant_retrieved / K` | RAG tests |
| **Turn Efficiency** | `successful_completions / total_turns` | Multi-turn tests |
| **Memory Recall Rate** | `correctly_recalled / total_stored` | Memory tests |
| **Entity Extraction F1** | `2 * P * R / (P + R)` | Social network tests |

---

## 9. Common Pitfalls & Troubleshooting

| Issue | Cause | Solution |
|-------|-------|---------|
| RAG returns empty results | File not indexed yet | Wait 5-10 seconds after upload; check `gemini_file_search_map.json` |
| Agent doesn't call expected tool | LLM decided differently | Check Step 2 decision in trajectory; adjust awareness instructions |
| Memory not persisting | EverMemOS not configured | Check `.evermemos/.env` for API keys; run `docker-compose up` for EverMemOS services |
| Job not executing | Job trigger daemon not running | Check tmux session for job_trigger process |
| Narrative switching unexpectedly | Continuity detection misjudge | Check embedding similarity scores in logs; consider setting `forced_narrative_id` |
| MCP server connection refused | Server not started | Check `module_runner.py` process in tmux; restart via `bash run.sh` |
| Agent takes too long | Large system prompt | Check system prompt size in trajectory; reduce number of active modules |
| `send_message_to_user_directly` missing | ChatModule not loaded | Ensure ChatModule instance is activated in Step 2 |

---

## 10. Summary: What to Test First

**For interns and new researchers**, start with these tests in order:

1. **RAG accuracy** -- Upload 5-10 documents, ask 20 questions, measure exact match rate
2. **Tool selection** -- Design 20 scenarios requiring specific tools, measure selection accuracy
3. **Multi-turn context** -- Run 5 multi-turn conversations (5-10 turns each), measure context retention
4. **Entity extraction** -- Mention 10 people across conversations, test recall accuracy
5. **Job scheduling** -- Create 5 job dependency chains, verify execution order

These tests require **no additional MCP servers** and exercise NexusMind's native capabilities.
