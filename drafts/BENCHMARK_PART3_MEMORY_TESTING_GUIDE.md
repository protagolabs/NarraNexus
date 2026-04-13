# NexusMind -- Conversational Memory Benchmark Testing Guide

**Part 3: Memory Benchmarks (LoCoMo, MemoryAgentBench, LongMemEval, and Others)**

---

## 1. The Core Challenge

Standard memory benchmarks (LoCoMo, MemoryAgentBench, LongMemEval) assume a simple model: give the agent a long conversation history, then ask questions about it. But NexusMind's memory architecture is **fundamentally different** -- it is designed to build memory **incrementally** through a 7-step pipeline that fires after every conversation turn:

```
Standard Benchmark Assumption:
    [300-turn conversation] → [load into agent] → [ask questions]

NexusMind's Actual Flow:
    Turn 1 → Step 0-6 (hooks fire: summarize, extract entities, write EverMemOS) →
    Turn 2 → Step 0-6 (hooks fire again) →
    ...
    Turn 300 → Step 0-6 →
    Now ask questions
```

This creates three specific problems:

| Problem | Description |
|---------|-------------|
| **Speed** | Running 300 turns through the full 7-step pipeline (with LLM calls at Steps 1, 2, 3, 4, 5) would take hours per conversation |
| **Narrative splitting** | NexusMind's ContinuityDetector may split a single LoCoMo conversation into multiple Narratives based on topic shifts |
| **Summary lossyness** | Each turn triggers `dynamic_summary` update and EverMemOS episode extraction -- the original verbatim conversation may be compressed/lost |

This guide provides **multiple injection strategies** ranked by fidelity and practicality.

---

## 2. Memory Benchmark Overview

### 2.1 LoCoMo (Long Conversational Memory)

**Source**: Snap Research / ACL 2024
**Paper**: [arXiv:2402.17753](https://arxiv.org/abs/2402.17753) | [GitHub: snap-research/locomo](https://github.com/snap-research/locomo)

| Aspect | Detail |
|--------|--------|
| Conversations | 10 (public) / 50 (full) |
| Turns per conversation | ~300 avg |
| Tokens per conversation | ~9,000 avg |
| Sessions per conversation | ~19 avg |
| Question types | Single-hop (36%), Multi-hop (15%), Temporal (21%), Open-domain (4%), Adversarial (25%) |
| Total QA pairs | 7,512 |
| Scoring | Token-level F1 (QA), ROUGE + FactScore (summarization) |

**Data format** (`locomo10.json`):
```json
{
  "sample_id": "locomo_1",
  "conversation": {
    "speaker_a": "Angela",
    "speaker_b": "Marcus",
    "session_1": [
      {"speaker": "Angela", "dia_id": "1_1", "text": "Hi Marcus! How was your weekend?"},
      {"speaker": "Marcus", "dia_id": "1_2", "text": "Great! I visited the art gallery."}
    ],
    "session_1_date_time": "2023-01-15 14:30",
    "session_2": [...],
    "session_2_date_time": "2023-02-03 10:00"
  },
  "qa": [
    {
      "question": "What did Marcus do over the weekend?",
      "answer": "Marcus visited a new art gallery downtown.",
      "category": "single-hop",
      "evidence": ["1_2"]
    }
  ]
}
```

### 2.2 MemoryAgentBench

**Source**: HUST / ICLR 2026
**Paper**: [arXiv:2507.05257](https://arxiv.org/abs/2507.05257) | [GitHub: HUST-AI-HYZ/MemoryAgentBench](https://github.com/HUST-AI-HYZ/MemoryAgentBench)

| Aspect | Detail |
|--------|--------|
| Scale | Up to 1.44M tokens per sequence |
| Input delivery | **Incremental chunks** (not full context) |
| Competencies | Accurate Retrieval, Test-Time Learning, Long-Range Understanding, Conflict Resolution |
| Scoring | SubEM, Accuracy, ROUGE-F1 |

Key difference from LoCoMo: MemoryAgentBench **feeds text chunks sequentially** with "memorize this" instructions, which is closer to NexusMind's turn-by-turn architecture.

### 2.3 LongMemEval

**Source**: Microsoft / ICLR 2025
**Paper**: [arXiv:2410.10813](https://arxiv.org/abs/2410.10813) | [GitHub: xiaowu0162/LongMemEval](https://github.com/xiaowu0162/LongMemEval)

| Aspect | Detail |
|--------|--------|
| Questions | 500 curated |
| Scale variants | S: ~40 sessions / ~115K tokens; M: ~500 sessions / ~1.5M tokens |
| Memory abilities | Extraction, Multi-session Reasoning, Temporal Reasoning, Knowledge Updates, Abstention |
| Scoring | LLM-as-Judge (GPT-4o, >97% human agreement) |
| Evaluation modes | **Online** (sequential session ingestion) and **Offline** (full history) |

### 2.4 Other Relevant Benchmarks

| Benchmark | Year | Scale | Key Focus |
|-----------|------|-------|-----------|
| **MSC** (Multi-Session Chat) | 2022 | 5 sessions | Persona consistency across sessions |
| **ConvoMem** | 2025 | 75,336 QA pairs | Changing facts, implicit connections; finds RAG needed beyond ~150 conversations |
| **Mem2ActBench** | 2026 | varies | Proactive memory-to-action: can the agent use memory to make correct tool calls? |

---

## 3. NexusMind Memory Layers vs. Benchmark Requirements

### 3.1 Which Memory Layers Are Tested by Which Benchmarks

```
┌─────────────────────────────────────────────────────────────────────────┐
│                Benchmark → Memory Layer Mapping                         │
├───────────────────────┬─────────────────────────────────────────────────┤
│                       │  NexusMind Memory Layer                         │
│ Benchmark Task        │  Chat    Narrative  EverMemOS  Social   All    │
│                       │  History  Summary   (LTM)      Graph           │
├───────────────────────┼─────────────────────────────────────────────────┤
│ Single-hop recall     │   ✓✓✓      ✓          ✓✓        -       -     │
│ Multi-hop reasoning   │   ✓✓       ✓✓         ✓✓        ✓       -     │
│ Temporal reasoning    │   ✓✓       ✓          ✓         -       -     │
│ Adversarial (abstain) │   -        -          -         -      ✓✓✓    │
│ Entity/person recall  │   ✓        -          -        ✓✓✓     -     │
│ Knowledge updates     │   ✓✓       ✓          ✓        ✓✓      -     │
│ Conflict resolution   │   ✓✓       -          ✓        ✓✓      -     │
│ Long-range summary    │   -       ✓✓✓         ✓✓        -       -     │
│ Cross-session reason  │   ✓(ST)    ✓✓         ✓✓✓       -       -     │
└───────────────────────┴─────────────────────────────────────────────────┘
LT = Long-term track, ST = Short-term track (cross-narrative)
```

### 3.2 Memory Layer Characteristics

| Layer | Fidelity | Capacity | Cross-Narrative | Best For |
|-------|----------|----------|-----------------|----------|
| **Chat History (LT)** | Verbatim messages | ~40 messages per turn (truncated at 4000 chars/msg) | No | Single-hop, recent recall |
| **Chat History (ST)** | Truncated to 200 chars | 15 most recent messages | Yes | Cross-topic awareness |
| **Narrative dynamic_summary** | LLM-compressed | 1 summary entry per event | No | Long-range understanding |
| **EverMemOS** | Episode-level summaries | Max 5 episodes per narrative, 1500 chars display | Yes | Cross-conversation semantic search |
| **Social Graph** | Entity attributes | Unlimited entities | Yes (agent-level) | Person/entity recall |

### 3.3 The Lossyness Problem

When a 300-turn conversation flows through NexusMind's pipeline, information is progressively compressed:

```
Original: 300 turns × ~30 tokens/turn = ~9,000 tokens (verbatim)
    ↓
Chat History (LT): Last ~40 turns preserved verbatim; older turns dropped
    ↓
Narrative Summary: ~1 sentence per turn = ~300 sentences (compressed)
    ↓
EverMemOS: 3-5 episodes × ~300 tokens each = ~1,500 tokens (heavily compressed)
    ↓
Social Graph: Entity attributes only (names, expertise, interaction counts)
```

**Implication**: For single-hop questions about Turn 5 in a 300-turn conversation, verbatim Chat History will have dropped it. The answer must survive through Narrative Summary or EverMemOS -- both lossy.

---

## 4. Injection Strategies

### Strategy 1: Full Pipeline Replay (Highest Fidelity, Slowest)

**Approach**: Replay each conversation turn through `AgentRuntime.run()`, letting all hooks fire naturally.

**When to use**: When you want to test NexusMind's memory **as it actually works in production** -- including all compression, summarization, and entity extraction.

```
For each LoCoMo conversation:
    For each session:
        For each turn (speaker_a message + speaker_b response):
            → AgentRuntime.run(
                agent_id, user_id,
                input_content=speaker_a_message,
                working_source=WorkingSource.CHAT
              )
            # All 7 steps execute:
            #   Step 1: Narrative selection (may create new or reuse)
            #   Step 2: Module loading
            #   Step 3: Agent responds (we DISCARD this response)
            #   Step 4: Persist (summary updated)
            #   Step 5: Hooks (EverMemOS write, entity extraction)
            #   Step 6: Callbacks
```

**Problems**:
- **Extremely slow**: ~30-60 seconds per turn × 300 turns = ~3-5 hours per conversation
- **Agent generates its own responses**: The agent's response replaces `speaker_b`'s original response
- **Narrative splitting**: Topic shifts in LoCoMo sessions may cause NexusMind to create multiple Narratives
- **Cost**: Hundreds of LLM API calls per conversation

**Mitigation for response replacement**: After the pipeline completes, overwrite the `final_output` in the Event record with the benchmark's original `speaker_b` response:
```python
# After AgentRuntime.run() completes:
await event_repo.update(event_id, {
    "final_output": original_speaker_b_response
})
# Also update chat instance memory with correct response
```

**Mitigation for Narrative splitting**: Force all turns into a single Narrative:
```python
await runtime.run(
    agent_id, user_id,
    input_content=speaker_a_message,
    forced_narrative_id="nar_locomo_conv1"  # Force same narrative
)
```

**Best for**: Small-scale testing (1-3 conversations), production-fidelity evaluation.

---

### Strategy 2: Selective Pipeline Replay (Moderate Fidelity, Moderate Speed)

**Approach**: Run only a subset of turns through the full pipeline, inject the rest directly into the database.

```
For each LoCoMo conversation:
    1. DB-inject ALL turns into Chat History (Strategy 3)
    2. Run every Nth turn (e.g., every 10th) through full pipeline
       → This triggers Narrative summary updates and EverMemOS writes
    3. Run the LAST 5 turns through full pipeline
       → This ensures recent context is fresh
```

**Advantages**:
- 10-30x faster than Strategy 1
- Narrative summaries and EverMemOS episodes still get created (via sampled turns)
- Social entities get extracted from sampled turns

**Disadvantages**:
- Summaries may miss details from non-sampled turns
- EverMemOS episodes are based on sampled turns only

**Best for**: Medium-scale testing (5-20 conversations), reasonable fidelity.

---

### Strategy 3: Direct Database Injection (Lowest Fidelity for Summary/LTM, Fastest)

**Approach**: Bypass the pipeline entirely. Write directly to each memory layer's database tables.

This is the **recommended approach for large-scale benchmarking** because it's fast, reproducible, and gives you full control over what goes into each memory layer.

#### 3a. Inject into Chat History

Write the full conversation into the `instance_json_format_memory_chat` table:

```python
import json
from datetime import datetime, timedelta

async def inject_chat_history(db_client, agent_id, user_id, conversation, instance_id, narrative_id):
    """
    Inject a LoCoMo conversation into ChatModule's instance memory.

    Args:
        conversation: LoCoMo conversation dict with session_1, session_2, etc.
        instance_id: e.g., "chat_locomo_001"
        narrative_id: e.g., "nar_locomo_001"
    """
    messages = []
    event_ids = []

    # Parse all sessions
    session_idx = 1
    while f"session_{session_idx}" in conversation:
        session_key = f"session_{session_idx}"
        date_key = f"session_{session_idx}_date_time"
        session_time = conversation.get(date_key, datetime.utcnow().isoformat())

        for turn in conversation[session_key]:
            dia_id = turn["dia_id"]
            event_id = f"evt_locomo_{dia_id}"
            event_ids.append(event_id)

            # Map LoCoMo speakers to user/assistant roles
            if turn["speaker"] == conversation["speaker_a"]:
                role = "user"
            else:
                role = "assistant"

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

    # Write to database
    memory = {
        "messages": messages,
        "last_event_id": event_ids[-1] if event_ids else "",
        "updated_at": datetime.utcnow().isoformat()
    }

    await db_client.execute(
        """INSERT INTO instance_json_format_memory_chat (instance_id, memory)
           VALUES (%s, %s) AS new_values
           ON DUPLICATE KEY UPDATE memory = new_values.memory""",
        params=(instance_id, json.dumps(memory, ensure_ascii=False))
    )

    return event_ids
```

#### 3b. Inject into Narrative

Create a Narrative record with pre-populated event_ids and summary:

```python
async def inject_narrative(db_client, agent_id, narrative_id, conversation, event_ids):
    """Create a Narrative with summary derived from LoCoMo's session_summary."""

    # Build dynamic_summary from LoCoMo's provided session summaries
    dynamic_summary = []
    session_idx = 1
    while f"session_{session_idx}_summary" in conversation.get("session_summary", {}):
        summary_text = conversation["session_summary"][f"session_{session_idx}_summary"]
        dynamic_summary.append({
            "event_id": f"evt_locomo_session_{session_idx}",
            "summary": summary_text,
            "timestamp": conversation["conversation"].get(
                f"session_{session_idx}_date_time", ""
            )
        })
        session_idx += 1

    narrative_data = {
        "narrative_id": narrative_id,
        "agent_id": agent_id,
        "type": "chat",
        "narrative_info": json.dumps({
            "name": f"LoCoMo: {conversation['sample_id']}",
            "description": f"Conversation between {conversation['conversation']['speaker_a']} "
                          f"and {conversation['conversation']['speaker_b']}",
            "current_summary": dynamic_summary[-1]["summary"] if dynamic_summary else ""
        }),
        "event_ids": json.dumps(event_ids),
        "dynamic_summary": json.dumps(dynamic_summary),
        "active_instances": json.dumps([]),
        "instance_history_ids": json.dumps([]),
        "topic_keywords": json.dumps([]),
        "topic_hint": "",
        "is_special": "default"
    }

    columns = ", ".join(narrative_data.keys())
    placeholders = ", ".join(["%s"] * len(narrative_data))
    update_clause = ", ".join([f"{k} = VALUES({k})" for k in narrative_data.keys()])

    await db_client.execute(
        f"""INSERT INTO narratives ({columns}) VALUES ({placeholders})
            ON DUPLICATE KEY UPDATE {update_clause}""",
        params=tuple(narrative_data.values())
    )
```

#### 3c. Inject into Events

Create Event records so the Event system is consistent:

```python
async def inject_events(db_client, agent_id, user_id, conversation, narrative_id):
    """Create Event records from LoCoMo turns."""

    session_idx = 1
    while f"session_{session_idx}" in conversation["conversation"]:
        session = conversation["conversation"][f"session_{session_idx}"]
        session_time = conversation["conversation"].get(
            f"session_{session_idx}_date_time", datetime.utcnow().isoformat()
        )

        # Group turns into user-assistant pairs
        i = 0
        while i < len(session):
            turn = session[i]
            event_id = f"evt_locomo_{turn['dia_id']}"

            user_input = turn["text"] if turn["speaker"] == conversation["conversation"]["speaker_a"] else ""
            assistant_output = ""

            # Look for the assistant response
            if i + 1 < len(session) and session[i+1]["speaker"] != turn["speaker"]:
                assistant_output = session[i+1]["text"]
                i += 2
            else:
                i += 1

            if user_input:
                await db_client.execute(
                    """INSERT INTO events
                       (event_id, narrative_id, agent_id, user_id, `trigger`,
                        trigger_source, env_context, module_instances, event_log,
                        final_output, created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON DUPLICATE KEY UPDATE final_output = VALUES(final_output)""",
                    params=(
                        event_id, narrative_id, agent_id, user_id,
                        "chat", user_id,
                        json.dumps({"input": user_input}),
                        json.dumps([]), json.dumps([]),
                        assistant_output,
                        session_time, session_time
                    )
                )

        session_idx += 1
```

#### 3d. Inject into EverMemOS

Write episode summaries to EverMemOS for cross-conversation retrieval:

```python
async def inject_evermemos(evermemos_client, conversation, narrative_id):
    """Write LoCoMo session summaries to EverMemOS as episodes."""

    narrative_name = f"LoCoMo: {conversation['sample_id']}"

    # Create conversation metadata
    await evermemos_client.create_conversation_meta(
        group_id=narrative_id,
        name=narrative_name,
        description=f"Conversation between {conversation['conversation']['speaker_a']} "
                    f"and {conversation['conversation']['speaker_b']}"
    )

    # Write each turn as a message
    for session_key in sorted(
        [k for k in conversation["conversation"] if k.startswith("session_") and not k.endswith("_date_time")]
    ):
        session_time = conversation["conversation"].get(
            f"{session_key}_date_time", datetime.utcnow().isoformat()
        )

        for turn in conversation["conversation"][session_key]:
            role = "user" if turn["speaker"] == conversation["conversation"]["speaker_a"] else "assistant"

            await evermemos_client.write_message({
                "message_id": f"{narrative_id}_{turn['dia_id']}",
                "create_time": session_time,
                "sender": turn["speaker"],
                "sender_name": turn["speaker"],
                "role": role,
                "type": "text",
                "content": turn["text"],
                "group_id": narrative_id,
                "group_name": narrative_name,
                "scene": "assistant"
            })
```

#### 3e. Inject into Social Graph

Pre-populate entities mentioned in the conversation:

```python
async def inject_social_entities(db_client, instance_id, conversation):
    """Create social entities for speakers in the conversation."""

    speakers = [
        conversation["conversation"]["speaker_a"],
        conversation["conversation"]["speaker_b"]
    ]

    for speaker in speakers:
        await db_client.execute(
            """INSERT INTO instance_social_entities
               (instance_id, entity_id, entity_type, entity_name,
                entity_description, interaction_count)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON DUPLICATE KEY UPDATE
                interaction_count = VALUES(interaction_count)""",
            params=(
                instance_id, speaker, "user", speaker,
                f"Speaker in LoCoMo conversation",
                len([t for s_key in conversation["conversation"]
                     if s_key.startswith("session_") and not s_key.endswith("_date_time")
                     for t in conversation["conversation"][s_key]
                     if t["speaker"] == speaker])
            )
        )
```

**Limitations of Strategy 3**:
- Narrative `dynamic_summary` must be manually constructed (can use LoCoMo's provided `session_summary`)
- EverMemOS episode extraction won't run (episodes must be injected as raw messages; EverMemOS processes them asynchronously)
- No routing embeddings generated for Narratives (semantic Narrative search won't work unless you compute and insert embeddings)
- Social entity descriptions are minimal (no LLM-generated personas)

**Best for**: Large-scale benchmarking, reproducibility, speed.

---

### Strategy 4: Hybrid -- DB Inject + EverMemOS Replay (Recommended)

**Approach**: Combine the speed of DB injection with the fidelity of EverMemOS's natural processing.

```
Step 1: DB-inject chat history, events, and narrative (Strategy 3a-3c)
        → Provides verbatim recall for ChatModule
        → ~seconds per conversation

Step 2: Write all messages to EverMemOS via HTTP API (Strategy 3d)
        → EverMemOS processes asynchronously:
           boundary detection → episode extraction → embedding
        → Provides long-term semantic memory
        → ~seconds for writing, minutes for processing

Step 3: (Optional) Run 5-10 key turns through full pipeline
        → Triggers social entity extraction
        → Generates Narrative routing embeddings
        → ~minutes per conversation

Step 4: Wait for EverMemOS processing to complete
        → Check EverMemOS API for episode availability

Step 5: Now ask benchmark questions via normal AgentRuntime.run()
```

**This is the recommended approach** because:
- Chat History is verbatim (no lossyness for recent turns)
- EverMemOS has full conversation data (episode extraction runs naturally)
- Fast enough for all 10-50 LoCoMo conversations
- Narrative summaries can use LoCoMo's provided `session_summary` data

---

## 5. Practical Step-by-Step Guide

### 5.1 Environment Preparation

```bash
# 1. Ensure all services are running
bash run.sh  # Select "Run"

# 2. Verify EverMemOS is running (if testing long-term memory)
docker ps | grep evermemos  # Should show MongoDB, Elasticsearch, Milvus, Redis

# 3. Create a benchmark agent
# Via frontend: http://localhost:5173 → Create Agent
# Note the agent_id (e.g., "agent_benchmark_001")

# 4. Download LoCoMo dataset
git clone https://github.com/snap-research/locomo.git /tmp/locomo
# Key file: /tmp/locomo/data/locomo10.json
```

### 5.2 Write the Injection Script

Create a Python script `scripts/inject_locomo.py`:

```python
"""
Inject LoCoMo benchmark conversations into NexusMind's memory system.

Usage:
    python scripts/inject_locomo.py \
        --locomo-file /tmp/locomo/data/locomo10.json \
        --agent-id agent_benchmark_001 \
        --user-id user_benchmark \
        --strategy hybrid
"""

import asyncio
import json
import argparse
from datetime import datetime

# Add project root to path
import sys
sys.path.insert(0, "src")

from xyz_agent_context.utils.db_factory import get_db_client


async def inject_single_conversation(db, agent_id, user_id, conv_data, strategy):
    sample_id = conv_data["sample_id"]
    narrative_id = f"nar_locomo_{sample_id}"
    instance_id = f"chat_locomo_{sample_id}"
    social_instance_id = f"social_locomo_{sample_id}"
    conversation = conv_data["conversation"]

    print(f"\n{'='*60}")
    print(f"Injecting {sample_id}...")

    # --- Step 1: Create module instance records ---
    await db.execute(
        """INSERT IGNORE INTO module_instances
           (instance_id, module_class, agent_id, user_id, narrative_id, is_public, status)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        params=(instance_id, "ChatModule", agent_id, user_id, narrative_id, 0, "active")
    )

    # --- Step 2: Build message list from all sessions ---
    messages = []
    event_ids = []
    session_idx = 1

    while f"session_{session_idx}" in conversation:
        session_key = f"session_{session_idx}"
        date_key = f"session_{session_idx}_date_time"
        session_time = conversation.get(date_key, datetime.utcnow().isoformat())

        for turn in conversation[session_key]:
            dia_id = turn["dia_id"]
            event_id = f"evt_locomo_{sample_id}_{dia_id}"
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

            # Create Event record
            env_context = {"input": turn["text"]}
            final_output = ""
            if role == "user":
                # Look ahead for assistant response
                idx = conversation[session_key].index(turn)
                if idx + 1 < len(conversation[session_key]):
                    final_output = conversation[session_key][idx + 1]["text"]

            await db.execute(
                """INSERT INTO events
                   (event_id, narrative_id, agent_id, user_id, `trigger`,
                    trigger_source, env_context, module_instances, event_log,
                    final_output, created_at, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON DUPLICATE KEY UPDATE updated_at = VALUES(updated_at)""",
                params=(
                    event_id, narrative_id, agent_id, user_id,
                    "chat", user_id,
                    json.dumps(env_context, ensure_ascii=False),
                    "[]", "[]",
                    final_output, session_time, session_time
                )
            )

        session_idx += 1

    total_sessions = session_idx - 1
    total_turns = len(messages)
    print(f"  Sessions: {total_sessions}, Turns: {total_turns}")

    # --- Step 3: Write chat history ---
    memory_doc = {
        "messages": messages,
        "last_event_id": event_ids[-1] if event_ids else "",
        "updated_at": datetime.utcnow().isoformat()
    }
    await db.execute(
        """INSERT INTO instance_json_format_memory_chat (instance_id, memory)
           VALUES (%s, %s) AS nv
           ON DUPLICATE KEY UPDATE memory = nv.memory""",
        params=(instance_id, json.dumps(memory_doc, ensure_ascii=False))
    )
    print(f"  Chat history: {total_turns} messages injected")

    # --- Step 4: Create Narrative with summaries ---
    dynamic_summary = []
    if "session_summary" in conv_data:
        for s_key in sorted(conv_data["session_summary"].keys()):
            idx = s_key.replace("session_", "").replace("_summary", "")
            dynamic_summary.append({
                "event_id": f"evt_locomo_{sample_id}_session_{idx}",
                "summary": conv_data["session_summary"][s_key],
                "timestamp": conversation.get(f"session_{idx}_date_time", "")
            })

    narrative_info = {
        "name": f"LoCoMo: {conversation['speaker_a']} & {conversation['speaker_b']}",
        "description": f"Long conversation between {conversation['speaker_a']} and {conversation['speaker_b']}",
        "current_summary": dynamic_summary[-1]["summary"] if dynamic_summary else ""
    }

    await db.execute(
        """INSERT INTO narratives
           (narrative_id, agent_id, type, narrative_info, event_ids,
            dynamic_summary, active_instances, instance_history_ids,
            topic_keywords, topic_hint, is_special, created_at, updated_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON DUPLICATE KEY UPDATE updated_at = VALUES(updated_at)""",
        params=(
            narrative_id, agent_id, "chat",
            json.dumps(narrative_info, ensure_ascii=False),
            json.dumps(event_ids),
            json.dumps(dynamic_summary, ensure_ascii=False),
            "[]", "[]", "[]",
            narrative_info["description"],
            "default",
            datetime.utcnow().isoformat(),
            datetime.utcnow().isoformat()
        )
    )
    print(f"  Narrative: created with {len(dynamic_summary)} session summaries")

    # --- Step 5: (Hybrid only) Write to EverMemOS ---
    if strategy == "hybrid":
        try:
            from xyz_agent_context.utils.evermemos.client import EverMemOSClient
            evermemos = EverMemOSClient(agent_id, user_id)

            # This writes messages; EverMemOS processes them asynchronously
            for msg in messages:
                await evermemos._post_message({
                    "message_id": msg["meta_data"]["event_id"],
                    "create_time": msg["meta_data"]["timestamp"],
                    "sender": conversation["speaker_a"] if msg["role"] == "user" else conversation["speaker_b"],
                    "sender_name": conversation["speaker_a"] if msg["role"] == "user" else conversation["speaker_b"],
                    "role": msg["role"],
                    "type": "text",
                    "content": msg["content"],
                    "group_id": narrative_id,
                    "group_name": narrative_info["name"],
                    "scene": "assistant"
                })
            print(f"  EverMemOS: {total_turns} messages written (processing async)")
        except Exception as e:
            print(f"  EverMemOS: SKIPPED ({e})")

    print(f"  Done: {sample_id}")


async def main(args):
    with open(args.locomo_file, "r") as f:
        locomo_data = json.load(f)

    db = await get_db_client()

    for conv_data in locomo_data:
        await inject_single_conversation(
            db, args.agent_id, args.user_id, conv_data, args.strategy
        )

    print(f"\n{'='*60}")
    print(f"All {len(locomo_data)} conversations injected.")
    print(f"Strategy: {args.strategy}")
    print(f"Agent: {args.agent_id}, User: {args.user_id}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--locomo-file", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--strategy", choices=["db-only", "hybrid"], default="hybrid")
    args = parser.parse_args()
    asyncio.run(main(args))
```

### 5.3 Run the Injection

```bash
cd /path/to/NexusAgent

python scripts/inject_locomo.py \
    --locomo-file /tmp/locomo/data/locomo10.json \
    --agent-id agent_benchmark_001 \
    --user-id user_benchmark \
    --strategy hybrid
```

### 5.4 Ask Benchmark Questions

After injection, ask the benchmark questions through normal conversation:

```python
"""
Run LoCoMo QA evaluation against injected conversations.
"""
import asyncio
import json

async def run_locomo_qa(agent_id, user_id, locomo_data):
    from xyz_agent_context.agent_runtime import AgentRuntime
    from xyz_agent_context.schema import WorkingSource

    results = []

    for conv_data in locomo_data:
        sample_id = conv_data["sample_id"]
        narrative_id = f"nar_locomo_{sample_id}"

        for qa in conv_data["qa"]:
            question = qa["question"]
            gold_answer = qa["answer"]
            category = qa["category"]

            # Ask the question, forcing it into the correct narrative
            runtime = AgentRuntime()
            response_text = ""

            async for msg in runtime.run(
                agent_id=agent_id,
                user_id=user_id,
                input_content=question,
                working_source=WorkingSource.CHAT,
                forced_narrative_id=narrative_id  # Critical: force correct narrative
            ):
                if hasattr(msg, "content"):
                    response_text += msg.content

            # Compute F1
            f1 = compute_token_f1(response_text, gold_answer)
            results.append({
                "sample_id": sample_id,
                "question": question,
                "gold": gold_answer,
                "predicted": response_text,
                "category": category,
                "f1": f1
            })

            print(f"[{category}] F1={f1:.2f} | Q: {question[:60]}...")

    # Aggregate
    for cat in ["single-hop", "multi-hop", "temporal", "open-domain", "adversarial"]:
        cat_results = [r for r in results if r["category"] == cat]
        if cat_results:
            avg_f1 = sum(r["f1"] for r in cat_results) / len(cat_results)
            print(f"\n{cat}: avg F1 = {avg_f1:.3f} ({len(cat_results)} questions)")


def compute_token_f1(prediction, gold):
    """Token-level F1 score (LoCoMo metric)."""
    pred_tokens = prediction.lower().split()
    gold_tokens = gold.lower().split()

    common = set(pred_tokens) & set(gold_tokens)
    if not common:
        return 0.0

    precision = len(common) / len(pred_tokens) if pred_tokens else 0
    recall = len(common) / len(gold_tokens) if gold_tokens else 0

    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)
```

### 5.5 Evaluation by Question Category

For each LoCoMo question category, expect different memory layers to be exercised:

| Category | Primary Memory Layer | What to Watch For |
|----------|---------------------|-------------------|
| **Single-hop** | Chat History (LT) | Can the agent find a specific fact from one turn? If the turn is old, check if EverMemOS or Narrative summary preserved it |
| **Multi-hop** | Chat History + Narrative Summary | Agent must synthesize info from multiple turns -- check if both turns are accessible |
| **Temporal** | Chat History (timestamps) + Narrative Summary | Agent must reason about time ordering -- check if `meta_data.timestamp` and `session_idx` are used |
| **Open-domain** | LLM's own knowledge | Not a memory test; agent should use world knowledge |
| **Adversarial** | All layers (abstention) | Agent should say "I don't know" -- check if it hallucinates by confusing memories |

---

## 6. MemoryAgentBench-Specific Guidelines

MemoryAgentBench's **incremental chunk delivery** is actually a better fit for NexusMind than LoCoMo, because NexusMind processes conversations turn-by-turn.

### Approach: Direct Turn-by-Turn Ingestion

```python
async def run_memory_agent_bench(agent_id, user_id, chunks, questions):
    """
    MemoryAgentBench feeds chunks sequentially, then asks questions.
    This maps directly to NexusMind's natural flow.
    """
    runtime = AgentRuntime()

    # Phase 1: Feed chunks (memorize phase)
    for i, chunk in enumerate(chunks):
        instruction = f"Please memorize the following content:\n\n{chunk}"

        async for msg in runtime.run(
            agent_id=agent_id,
            user_id=user_id,
            input_content=instruction,
            working_source=WorkingSource.CHAT
        ):
            pass  # Discard responses during memorization

        if i % 10 == 0:
            print(f"  Ingested chunk {i+1}/{len(chunks)}")

    # Phase 2: Ask questions
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

        results.append({
            "question": qa["question"],
            "gold": qa["answer"],
            "predicted": response,
            "match": qa["answer"].lower() in response.lower()
        })

    return results
```

### Chunk Size Considerations

| MemoryAgentBench Chunk Size | NexusMind Implications |
|----------------------------|----------------------|
| 512 tokens | ~1 turn of conversation; fits naturally |
| 4096 tokens | ~10-15 turns; Chat History will store verbatim; may hit single-message truncation (4000 char limit) |

**Recommendation**: Use 512-token chunks for NexusMind to align with the per-turn architecture.

---

## 7. LongMemEval-Specific Guidelines

### Online Mode (Recommended)

LongMemEval's **online mode** maps well to NexusMind: sessions are delivered sequentially, and the agent builds memory as it goes.

```python
async def run_longmemeval_online(agent_id, user_id, sessions, questions):
    """
    LongMemEval online mode: feed sessions one at a time.
    """
    runtime = AgentRuntime()

    # Phase 1: Ingest sessions
    for session_id, session_turns in sessions.items():
        for turn in session_turns:
            if turn["role"] == "user":
                async for msg in runtime.run(
                    agent_id=agent_id,
                    user_id=user_id,
                    input_content=turn["content"],
                    working_source=WorkingSource.CHAT
                ):
                    pass

    # Phase 2: Ask questions
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

        # Score with LLM-as-Judge
        score = await llm_judge(qa["question"], qa["answer"], response)
```

### Scale Considerations

| Variant | Sessions | Tokens | Estimated Ingestion Time | Feasible? |
|---------|----------|--------|-------------------------|-----------|
| LongMemEval_S | ~40 | ~115K | ~20-40 min (full pipeline) | Yes |
| LongMemEval_M | ~500 | ~1.5M | ~4-8 hours (full pipeline) | Use DB injection for most, pipeline for last 20 |

---

## 8. Testing Each Memory Layer in Isolation

To understand which memory layer contributes what, run ablation tests:

### Ablation A: Chat History Only

Disable EverMemOS and Social Network hooks. Only ChatModule's instance memory is active.

```python
# In agent awareness, add:
"IMPORTANT: Do not use long-term memory search. Only rely on
 your direct conversation history to answer questions."
```

### Ablation B: EverMemOS Only

Clear the Chat History after injection, keep only EverMemOS data. This tests whether the episodic memory system can answer questions on its own.

```sql
-- Clear chat history for the test narrative
DELETE FROM instance_json_format_memory_chat
WHERE instance_id LIKE 'chat_locomo_%';
```

### Ablation C: Narrative Summary Only

Clear both Chat History and EverMemOS. Only the Narrative's `dynamic_summary` remains.

### Ablation D: Social Graph Only (Entity Questions)

For entity-related questions (LoCoMo questions about people), test whether the Social Graph alone can answer:

```python
# Pre-extract entities from LoCoMo's observation data
for observation in conv_data["observation"]["session_1_observation"]:
    # Parse: "Marcus visited a new art gallery downtown"
    # → Entity: Marcus, action: visited art gallery
    pass
```

---

## 9. Metrics and Evaluation

### 9.1 Per-Layer Metrics

| Metric | What It Measures | How to Compute |
|--------|-----------------|----------------|
| **Chat History Recall** | Can ChatModule retrieve the relevant turn? | Check if the answer's evidence turn exists in `instance_json_format_memory_chat` |
| **EverMemOS Episode Hit Rate** | Does the relevant episode get retrieved? | Check EverMemOS search results for the query |
| **Narrative Summary Coverage** | Is the fact preserved in the summary? | Search `dynamic_summary` entries for the answer |
| **Social Entity Accuracy** | Are entity attributes correct? | Compare `instance_social_entities` records against ground truth |

### 9.2 Benchmark-Specific Metrics

| Benchmark | Primary Metric | Implementation |
|-----------|---------------|----------------|
| **LoCoMo** | Token-level F1 | `compute_token_f1(predicted, gold)` |
| **MemoryAgentBench** | Substring Exact Match (SubEM) | `gold.lower() in predicted.lower()` |
| **LongMemEval** | LLM-as-Judge | GPT-4o evaluates (binary correct/incorrect) |

---

## 10. Known Limitations and Workarounds

| Limitation | Impact | Workaround |
|-----------|--------|------------|
| Chat History truncation (40 msg/turn, 4000 chars/msg) | Old turns dropped from LT history | Inject into EverMemOS for long-range recall |
| Narrative splitting on topic change | Single LoCoMo conversation may become multiple Narratives | Use `forced_narrative_id` parameter |
| dynamic_summary is lossy | Fine-grained details lost in summaries | Use LoCoMo's provided `session_summary` as ground truth summaries |
| EverMemOS async processing delay | Episodes may not be ready immediately after injection | Wait 30-60 seconds, then verify via EverMemOS API |
| Short-term memory is cross-narrative only | Won't help within a single LoCoMo conversation | Not a limitation for single-narrative tests |
| No temporal indexing in Chat History | Time-based questions rely on `meta_data.timestamp` | Ensure timestamps are injected correctly from LoCoMo's `session_N_date_time` |
| Social entity extraction requires LLM | DB injection won't auto-extract entities | Pre-extract from LoCoMo's `observation` data and inject manually |

---

## 11. Recommended Testing Plan

| Phase | Action | Time Estimate |
|-------|--------|---------------|
| **Phase 1** | Inject 2 LoCoMo conversations (hybrid strategy) | 10-15 min |
| **Phase 2** | Run QA on injected conversations, compute F1 by category | 30-60 min |
| **Phase 3** | Run ablation tests (Chat-only, EverMemOS-only, Summary-only) | 2-3 hours |
| **Phase 4** | Inject all 10 LoCoMo conversations | 30-60 min |
| **Phase 5** | Full LoCoMo QA evaluation | 2-4 hours |
| **Phase 6** | MemoryAgentBench (incremental, subset of tasks) | 4-8 hours |
| **Phase 7** | LongMemEval_S (40 sessions, online mode) | 2-4 hours |
| **Phase 8** | Analysis: per-layer contribution, failure modes | 1 day |
