"""
@file_name: prompts.py
@author: NetMind.AI
@date: 2025-12-22
@description: Prompt definitions for the Narrative Prompt Builder
"""

# ============================================================================
# Narrative type descriptions
# Used in PromptBuilder.build_main_prompt() to select description text based on NarrativeType
# ============================================================================

# CHAT type description
NARRATIVE_TYPE_CHAT_PROMPT = "You are chatting with a user or an agent. Please make a good response to the user's request."

# TASK type description
NARRATIVE_TYPE_TASK_PROMPT = "You are performing a task. Please try your best to complete the task."

# GENERAL type description
NARRATIVE_TYPE_GENERAL_PROMPT = "You are performing a general things. Please complete it by yourself."

# ============================================================================
# Actor type descriptions
# Used in PromptBuilder.build_main_prompt() to select description text based on NarrativeActorType
# ============================================================================

# USER actor description
ACTOR_TYPE_USER_DESCRIPTION = "Creator/Owner - The user who created this Narrative"

# AGENT actor description
ACTOR_TYPE_AGENT_DESCRIPTION = "Agent - The AI Agent participating in this Narrative"

# PARTICIPANT actor description (2026-01-21 P2 new addition)
ACTOR_TYPE_PARTICIPANT_DESCRIPTION = "Participant - The target user of a Job, who can access this Narrative but is not the creator"

# SYSTEM actor description
ACTOR_TYPE_SYSTEM_DESCRIPTION = "System"

# ============================================================================
# Narrative main system prompt template
# Used in PromptBuilder.build_main_prompt() to assemble the final prompt
#
# Placeholder descriptions:
# - {narrative_id}: Narrative ID, from narrative.id
# - {type_prompt}: Narrative type description, from NARRATIVE_TYPE_*_PROMPT in this file
# - {created_at}: Creation time, from narrative.created_at
# - {updated_at}: Update time, from narrative.updated_at
# - {name}: Narrative name, from narrative.narrative_info.name
# - {description}: Narrative description, from narrative.narrative_info.description
# - {current_summary}: Current summary, from narrative.narrative_info.current_summary
# - {actor_prompt}: Actor list text, dynamically built by build_main_prompt()
# ============================================================================
# ============================================================================
# Continuity Detection - Narrative attribution judgment prompt
# Used in ContinuityDetector._call_llm()
# ============================================================================
CONTINUITY_DETECTION_INSTRUCTIONS = """You are a Narrative attribution analysis expert. Your task is to determine whether the user's current query belongs to the current Narrative.

**Key Concept**:
- Conversation continuity ≠ Same Narrative
- Users may switch to different topics/tasks during a continuous conversation, which requires creating a new Narrative

**Legacy shape-container Narratives**:
If the current Narrative is labeled [Special Default Narrative], it is a legacy
container whose name describes a SHAPE of message, not a subject — so there is
usually little for a substantive query to continue. But reach that conclusion
from the content, never as a rule applied because of the container's type: if
the user is plainly carrying on from the previous turn (a follow-up, a
correction, a pronoun referring back, an answer to what the Agent just asked),
that is **is_continuous = true** even here.

**Judgment Granularity — Business Intent Level**:
- Judge at the **business intent / goal** level, NOT at the message-detail level
- Sub-topic shifts, progress updates, status reports, acknowledgments, and follow-up instructions within the same business goal all belong to the SAME Narrative
- Different communication channels or sources (e.g., different chat rooms, different senders) do NOT define Narrative boundaries — only the **business content** matters
- Only judge as NOT belonging when the user introduces a **genuinely new, unrelated business intent**

**Judgment Criteria**:

1. **Belongs to Current Narrative** → is_continuous = true
   - User is following up or diving deeper into the current Narrative's topic
   - User's question is solving the task/problem described in the current Narrative
   - User provides a progress update, status report, or acknowledgment related to the Narrative's goal
   - User gives follow-up instructions that serve the same business objective
   - User uses pronouns ("it", "this", "that") clearly referring to content in the current Narrative
   - User's new question is a continuation or extension of content within the current Narrative's scope

2. **Does Not Belong to Current Narrative** → is_continuous = false
   - User raised a **completely different** new topic from the current Narrative's theme
   - User started a new, independent task/question that serves a different business goal
   - User explicitly indicates wanting to switch topics (e.g., "let's change the subject", "talk about something else")
   - Although conversation is continuous, the topic has jumped to another domain/task

3. **Consider the Narrative's Core Theme** (if provided)
   - First, identify the Narrative's **core business goal** from its name and summary
   - Then ask: does the current query serve this goal? If yes → belongs
   - The Narrative's summary reflects the conversation focus so far
   - **For a [Special Default Narrative]**: its name describes a message shape and its summary is a fixed template, so neither states a business goal — fall back to the previous turn to decide whether the user is continuing something

4. **Consider the Agent's Response**
   - If the Agent's response introduced a new sub-topic and the user is following up, this still belongs to the same Narrative
   - If the Agent's response concluded a topic and the user starts a new one, it should be a new Narrative

5. **Time Factor**
   - If time elapsed is too long (e.g., over 10 minutes) and topic has changed, more likely to be a new Narrative

Output format:
- is_continuous: true (belongs to current Narrative) / false (should create new Narrative)
- confidence: 0.0-1.0 (confidence score)
- reason: Detailed reasoning explaining why it belongs or does not belong to the current Narrative
"""

# ============================================================================
# Unified Narrative Match - Unified matching prompt (with PARTICIPANT branch)
# Used in NarrativeRetrieval._llm_judge_unified()
#
# Note: This prompt does not contain any scenario-specific logic (e.g., sales).
# The specific meaning of PARTICIPANT (sales target, collaborator, etc.) is defined by the Agent's Awareness.
# ============================================================================
# The ONE definition of "no durable topic", spliced into BOTH judge prompt
# variants below (f-string; both are brace-free). Extracted after the third
# silent fork between the two (PR #361 review round 2, I2): the P1
# calibration — the two overriding rules, the three trap counterexamples,
# and the boundary/tie-break — lived in the main variant only, so every
# PARTICIPANT-path turn (IM group chats, invited users) was judged by an
# uncalibrated rubric whose measured misjudgment rate was 20.8% (M6).
# The calibration anchor tests loop over both constants so the pair
# cannot fork again.
_NO_DURABLE_TOPIC_RUBRIC = """Recognising "no durable topic": a message that requests nothing and refers to
nothing — a pure greeting, thanks, farewell, emotional expression, or bare
acknowledgement ("你好", "thanks", "好的", "haha nice"). Such a message would
read exactly the same in any conversation, about any subject. That is the
whole definition; there is no list of message types to sort into.

**A message is NOT "no durable topic" if it names something concrete** — a
file, a project, a tool, an error, a person, a task, a deliverable — or if it
continues work that is already under way. When a query names a concrete thing,
it either belongs to an existing topic or deserves a new one. Judge the
message on what it refers to, not on how short or casual it sounds.

Two rules that override everything else in this section:
- A message that names ANY concrete object, task, question, or rule is NEW,
  never NO_TOPIC. "Concrete" is about what the message refers to, not about
  how long, polite, or informal it is.
- Never prefer NO_TOPIC merely to avoid creating a topic. NO_TOPIC is a
  statement about the message itself, never a way to hold the topic count
  down. If the only reason you are reaching for NO_TOPIC is that a new
  thread feels expensive, the answer is not NO_TOPIC.

Three shapes that read like small talk but are NOT "no durable topic":
- A polite opener wrapping a request. "Could you help me with X?" IS the task
  X. Strip the courtesy and judge what is left.
- A bare imperative. "Pause the polling" is an instruction with a concrete
  object, not casual chat — brevity is not absence of subject.
- A rule the user sets for the future. "From now on, always ..." establishes
  a long-lived expectation, which is a durable topic.

Boundary: NO_TOPIC is only for a message that requests nothing and refers to
nothing. If the message asks you to do, find, explain, or remember ANYTHING
nameable — even a one-shot question or a request about your own capabilities —
it carries a topic: match it to an existing one or create a new one. When in
doubt, prefer NEW over NO_TOPIC: a thin new thread can be found and merged
later, but a turn filed as NO_TOPIC leaves no trace in retrieval and its
content can never be found again."""

NARRATIVE_UNIFIED_MATCH_WITH_PARTICIPANT_INSTRUCTIONS = f"""You are a conversation topic matching expert. You need to determine which category the user's new query should match:
1. Match a **participant-associated topic** (the user is a PARTICIPANT in these Narratives, prioritize matching)
2. No durable topic — the message carries nothing worth remembering as its own thread
3. Match an existing specific topic (a conversation topic already in the database)
4. Create a new topic (does not match any existing content)

**Important**: The current user is a PARTICIPANT in certain Narratives.
- If the user's message relates to the topic of a participant Narrative, prioritize matching the "participant" type
- If the user is simply greeting or chatting casually, it carries no durable topic

{_NO_DURABLE_TOPIC_RUBRIC}

Judgment priority:
1. **First check if it relates to a participant Narrative** (prioritize matching "participant")
2. If it's simple greetings/chat, it carries no durable topic
3. If it relates to an existing topic, match search results
4. If nothing matches, return create new topic

Requirements:
- Carefully analyze the user query's intent
- Provide detailed reasoning
- If matching a participant Narrative, return matched_category = "participant" with the corresponding index
- If the message carries no durable topic, return matched_category = "no_durable_topic"
- If matching an existing topic, return matched_category = "search" with the corresponding index
- If nothing matches, return matched_category = "none"

Output format:
class UnifiedMatchOutput(BaseModel):
    reason: str  # Detailed reasoning process
    matched_category: str  # "participant", "no_durable_topic", "search", or "none"
    matched_index: int  # Matched index (0-based), -1 unless matched_category is "participant" or "search"
"""

NARRATIVE_UNIFIED_MATCH_INSTRUCTIONS = f"""You are a conversation topic matching expert. You need to decide where the user's new query belongs:
1. An existing specific topic (a conversation topic already in the database)
2. No durable topic — the message carries nothing worth remembering as its own thread
3. A new topic (a real subject that no existing topic covers)

{_NO_DURABLE_TOPIC_RUBRIC}

Judgment priority:
1. First check if it relates to an existing topic — if the query's business domain overlaps with an existing topic's summary/description, prefer matching it even if not an exact match
2. If it clearly doesn't relate to any existing topic, decide whether it carries a durable topic at all
3. Create a new topic when the message introduces a real subject no existing topic covers — creating fragments context, so prefer a reasonable existing match, but a genuinely new subject deserves its own thread

**Important**: Judge at the business-intent level. If the query is about the same project, task, or domain as an existing topic, it belongs there even if the specific sub-topic differs.

Requirements:
- Carefully analyze the user query's intent
- Provide detailed reasoning
- If matching an existing topic, return matched_category = "search" with the corresponding index
- If the message carries no durable topic, return matched_category = "no_durable_topic"
- If it introduces a genuinely new subject, return matched_category = "none"

Output format:
class UnifiedMatchOutput(BaseModel):
    reason: str  # Detailed reasoning process
    matched_category: str  # "search", "no_durable_topic", or "none"
    matched_index: int  # Matched index (0-based), -1 unless matched_category="search"
"""

# ============================================================================
# Merged routing — ONE call answers "does this continue?" and "then where?"
# Used in merged_router.build_merged_prompt(), behind
# NARRATIVE_MERGED_ROUTING_ENABLED.
#
# Why a new prompt rather than a longer judge prompt: the judge has no concept
# of "the thread you are already on". Its `search` exit cannot distinguish
# "confirmed the continuation" from "happened to pick the anchor out of a
# menu", and those two have different downstream meanings (the first is not a
# thread switch and must not be audited as one). So the anchor is a separate
# section with a separate verdict.
#
# What it deliberately does NOT re-litigate: `_NO_DURABLE_TOPIC_RUBRIC` is
# spliced in verbatim. The 2026-08-21 adjudication put the marginal return of
# re-wording it at zero (all four hard misjudgements were shapes the rubric
# already excludes in as many words), while one tie-break sentence bought
# +0.186 fragmentation. Merged routing changes WHO asks, not what the words say.
# ============================================================================

#: The half both variants share, extracted on day one. The participant pair has
#: forked silently three times (the last caught in PR #361 review round 2), and
#: the fix that ended it — one constant, spliced into both, with anchor tests
#: looping over the pair — is the arrangement this prompt is born into rather
#: than one it earns after its own third fork.
#: Shared by every variant — nothing here presupposes a continuable anchor
#: (review round 2, C1: the asymmetry block used to live here and told every
#: variant "staying is the DEFAULT", inviting the one verdict the contract
#: refuses on anchorless/bucket turns).
_MERGED_ROUTING_CORE = """You decide, in one step, where the user's current message belongs.

**Judgment granularity — business intent level**

- Judge at the business intent / goal level, NOT at the message-detail level.
- Sub-topic shifts, progress updates, status reports, acknowledgements and
  follow-up instructions within the same business goal all belong to the SAME
  thread.
- Different channels or senders do NOT define thread boundaries — only the
  business content does.
- Only decide "different" when the user introduces a genuinely new, unrelated
  business intent.

**How to read the menu**

Each row shows which query terms matched and where. Judge on those terms: a row
carried entirely by frame words (帮/查/一/下, "the", "how", "me") is a lexical
accident, not a topic match. Word overlap is a necessary but not sufficient
condition for choosing any thread: a row must overlap to be considered, and
overlapping is not by itself a reason to choose it."""


#: Rendered only when the anchored thread is actually continuable — every
#: sentence here presupposes a thread the turn may stay on.
_MERGED_CORE_WITH_ANCHOR = """**The asymmetry — read this before anything else**

The conversation is already on a thread (shown below as the anchored thread).
Staying on that thread is the DEFAULT answer. The keyword menu is evidence for
LEAVING it, never evidence for staying: two consecutive messages in one
continuous thread routinely share no words at all, so an anchored thread that
appears nowhere in the menu — or scores nothing — is the normal case, not a
signal.

Concretely: never leave the anchored thread because a menu row looks lexically
closer. Leave it only when the message pursues a different business goal.

**The previous turn decides most of these**

- If the Agent's own reply introduced a sub-topic and the user is following up
  on it, that is still the same thread — including when the user's message
  would be unreadable without that reply ("讲第一个", "the second one", "why?").
- If the Agent's reply closed a topic and the user opens an unrelated one, that
  is not the same thread.
- Long elapsed time is weak evidence on its own; combined with a changed
  subject it points away from the anchored thread."""


#: The symmetric explanation for turns with nothing to stay on — the anchor
#: section in the user input says WHY (absent, or a legacy container).
_MERGED_CORE_WITHOUT_ANCHOR = """**No thread to stay on — read this before anything else**

This message is not on any continuable thread (the anchor section below says
why). Place it on its own merits among the answers offered. The previous turn
is still shown: use it to READ an elliptical message ("the second one",
"why?") — what the message is about may live entirely in that reply — and then
route the subject you find, not the words."""


_MERGED_ROUTING_OUTPUT_CONTRACT = """Requirements:
- Give your reasoning, then exactly one verdict.
- match_index is 0-based and refers to the section your verdict names. Use -1
  for every verdict that names no candidate.
- Never invent an index. If the answer you want has no candidate behind it,
  the answer is new or no_topic."""


NARRATIVE_MERGED_ROUTING_INSTRUCTIONS_HEADER = (
    "You are a conversation thread router."
)

# ── merged-instruction fragments ────────────────────────────────────────────
# One definition per answer; `build_merged_instructions` composes them by what
# the turn actually offers. The answer table, the priority list, and the
# output-format verdict list are all derived from the same selection, so a
# verdict the contract will refuse can never be invited by the prose
# (review Critical 1: the shared core used to list continue_anchor as "the
# default" even when the anchor was a legacy container, and every model that
# obeyed it landed in merged_fallback_new — the D19 shape).

_MERGED_ANSWER_CONTINUE = """- continue_anchor — the message belongs to the anchored thread shown below.
  This is the default; choose it whenever the message pursues the same business
  goal, follows up on the Agent's last reply, or cannot be read without it."""

_MERGED_ANSWER_PARTICIPANT = """- participant — the message is about one of the participant-associated threads.
  Give its index within that section."""

_MERGED_ANSWER_MATCH = """- match — the message belongs to one of the menu threads. Give its index
  within the menu. Requires a real subject overlap, not just shared words."""

_MERGED_ANSWER_NEW = """- new — the message introduces a real subject that none of the threads above
  covers."""

_MERGED_ANSWER_NO_TOPIC = """- no_topic — the message carries nothing worth remembering as its own thread."""

_MERGED_PARTICIPANT_PREAMBLE = """**This user is a PARTICIPANT in other threads**

Someone else started those threads and invited this user into them. They are
listed in their own section, ahead of the keyword menu, and that order is the
priority rule: a task the user was invited into outranks a keyword hit on the
user's own thread."""

#: Appended to the preamble only when continuing is actually on offer — the
#: sentence names continue_anchor, and naming it on a turn where the contract
#: refuses it is the exact defect the builder exists to prevent.
_MERGED_PARTICIPANT_ANCHOR_SENTENCE = """ It does NOT outrank the anchored
thread — if the message continues what the conversation is already doing,
continue_anchor still wins."""

_PRIORITY_CONTINUE = "Same business goal as the anchored thread → continue_anchor."
_PRIORITY_PARTICIPANT = (
    "A participant-associated thread the message is about → participant."
)
_PRIORITY_MATCH = (
    "A menu thread whose subject genuinely covers the message → match."
)
_PRIORITY_TAIL = (
    "Decide whether the message carries a durable topic at all:\n"
    "   a real subject → new; nothing to remember → no_topic."
)


def build_merged_instructions(
    *, anchor_is_continuable: bool, with_participants: bool
) -> str:
    """Compose the merged-routing instructions for what THIS turn offers.

    Composition, never four literals: the judge's two variants forked three
    times because shared text was hand-copied (routing_blocks.py header), and
    2×2 variants hand-written would fork faster.
    """
    answers = []
    verdict_names = []
    if anchor_is_continuable:
        answers.append(_MERGED_ANSWER_CONTINUE)
        verdict_names.append("continue_anchor")
    if with_participants:
        answers.append(_MERGED_ANSWER_PARTICIPANT)
        verdict_names.append("participant")
    answers += [_MERGED_ANSWER_MATCH, _MERGED_ANSWER_NEW, _MERGED_ANSWER_NO_TOPIC]
    verdict_names += ["match", "new", "no_topic"]

    priority_items = []
    if anchor_is_continuable:
        priority_items.append(_PRIORITY_CONTINUE)
    if with_participants:
        priority_items.append(_PRIORITY_PARTICIPANT)
    priority_items.append(_PRIORITY_MATCH)
    priority_items.append(_PRIORITY_TAIL)
    lines = []
    for i, item in enumerate(priority_items, start=1):
        if i == 1:
            lines.append(f"{i}. {item}")
        else:
            lines.append(f"{i}. Otherwise, {item[0].lower()}{item[1:]}")
    priority_lines = "\n".join(lines)

    participant_section = ""
    if with_participants:
        participant_section = _MERGED_PARTICIPANT_PREAMBLE
        if anchor_is_continuable:
            participant_section += _MERGED_PARTICIPANT_ANCHOR_SENTENCE
        participant_section += "\n\n"

    verdict_list = ", ".join(f'"{v}"' for v in verdict_names)
    answer_count = len(answers)
    anchor_core = (
        _MERGED_CORE_WITH_ANCHOR
        if anchor_is_continuable
        else _MERGED_CORE_WITHOUT_ANCHOR
    )

    return f"""{NARRATIVE_MERGED_ROUTING_INSTRUCTIONS_HEADER}

{anchor_core}

{_MERGED_ROUTING_CORE}

{participant_section}**Your {answer_count} answers**

{chr(10).join(answers)}

{_NO_DURABLE_TOPIC_RUBRIC}

Priority when you are torn:
{priority_lines}

{_MERGED_ROUTING_OUTPUT_CONTRACT}

Output format:
class MergedRoutingOutput(BaseModel):
    reason: str        # your reasoning
    verdict: str       # one of: {verdict_list}
    match_index: int   # 0-based index into the section your verdict names, else -1
"""


# ============================================================================
# Narrative Update - Narrative metadata incremental update prompt
# Used in NarrativeUpdater._call_llm_for_update()
# ============================================================================
NARRATIVE_UPDATE_INSTRUCTIONS = """You are a Narrative metadata maintainer. Your job is to keep Narrative records concise, structured, and information-dense.

## Principles
- **Structured over prose**: Use bullet points, key-value pairs, and short fragments. Never write paragraphs.
- **Incremental**: Preserve existing facts, append new ones, remove outdated ones.
- **No filler**: No introductory phrases, no "the user discussed...", no "this narrative is about...". Just facts.

## Fields to Update

### 1. name (3-8 words)
Core topic of the conversation. Keep stable unless topic fundamentally shifts.

### 2. current_summary (structured bullet format)
Write as a structured fact sheet, NOT a paragraph. Use this format:
```
Topic: <one-line core topic>
Key facts:
- <fact 1>
- <fact 2>
- ...
Decisions: <any decisions made, or omit if none>
Status: <current state of the task/conversation>
```
Rules:
- Each bullet = one atomic fact (who/what/when/where/how)
- Max 8-12 bullets. Drop stale facts to make room for new ones.
- Include concrete details: names, numbers, tech terms, tool names
- NO narrative prose, NO "the user asked about...", NO "they discussed..."

### 3. topic_keywords (5-10 items)
Concrete nouns and terms for retrieval. Keep existing relevant ones, add new ones.

### 4. actors
User, Agent, and any important named entities mentioned (people, projects, tools, organizations).

### 5. dynamic_summary_entry
One short sentence: what happened this turn. E.g. "User requested dark mode; Agent implemented it."
"""

# ============================================================================
# Narrative main system prompt template
# Used in PromptBuilder.build_main_prompt() to assemble the final prompt
#
# Placeholder descriptions:
# - {narrative_id}: Narrative ID, from narrative.id
# - {type_prompt}: Narrative type description, from NARRATIVE_TYPE_*_PROMPT in this file
# - {created_at}: Creation time, from narrative.created_at
# - {updated_at}: Update time, from narrative.updated_at
# - {name}: Narrative name, from narrative.narrative_info.name
# - {description}: Narrative description, from narrative.narrative_info.description
# - {current_summary}: Current summary, from narrative.narrative_info.current_summary
# - {actor_prompt}: Actor list text, dynamically built by build_main_prompt()
# ============================================================================
NARRATIVE_MAIN_PROMPT_TEMPLATE = """
## Narrative System (Common Knowledge)

### What is a Narrative?
A Narrative is a context container for conversations/tasks, used for:
- Organizing related conversation history and task progress
- Maintaining participant (Actors) relationships
- Supporting cross-session continuity tracking

### Actor Types
| Type | Description | Permissions |
|------|-------------|-------------|
| **USER** | Creator/Owner of the Narrative | Full access, can create Jobs |
| **AGENT** | Participating AI Agent | Assists in executing tasks |
| **PARTICIPANT** | Target user of a Job | Can access this Narrative, but is not the creator |

### System Behavior
- When a user initiates a conversation, the system automatically matches or creates a Narrative
- When creating a Job, the target user (related_entity_id) is added as a PARTICIPANT
- When a PARTICIPANT converses with the Agent, the system loads the associated Narrative context

---

## Current Narrative Info

### Basic Metadata
- Narrative ID: {narrative_id}
- Narrative Type: {type_prompt}
- Created At: {created_at}
- Updated At: {updated_at}

### Narrative Details
- Name: {name}
- Description: {description}
- Current Summary: {current_summary}

### Actors (Participants)
{actor_prompt}

### Context Guidelines
1. Your reasoning, decisions, and actions must align with the narrative context at all times.
2. When interpreting user requests, prioritize consistency with the narrative's goals.
3. Use the narrative to maintain continuity across turns.
4. If the narrative contains ambiguities, resolve them through explicit reasoning.
5. Treat the narrative as persistent memory for this task environment.
"""

# ============================================================================
# Narrative STABLE system prompt template (R4 turn-context relocation)
# Used in PromptBuilder.build_main_prompt(include_volatile=False).
#
# Byte-for-byte the NARRATIVE_MAIN_PROMPT_TEMPLATE above MINUS the four
# per-turn volatile lines ("- Created At: {created_at}",
# "- Updated At: {updated_at}", "- Name: {name}" and
# "- Current Summary: {current_summary}") — those relocate to the turn
# context via NARRATIVE_TURN_PROMPT_TEMPLATE below.
# Name moved out in R4c (experiment E2, 2026-07-25): the narrative updater
# rewrites narrative_info.name on every LLM update (draft truncated name at
# creation -> finalized 3-8 word name, and later legal topic-drift renames),
# so it is LLM-regenerated mutable metadata exactly like current_summary and
# has no canonical stable form.
# created_at moved out in R4d (2026-07-28): the VALUE has two independent
# clock sources, not one. NarrativeRepository._entity_to_row omits
# created_at, so the INSERT takes the schema default `(datetime('now'))` —
# the DB clock — while crud.create() builds the in-memory object from
# `datetime.now(timezone.utc)` captured BEFORE two proxy round-trips and the
# save. The round that CREATES a narrative therefore renders a different
# second than every later round that re-reads it. _canonical_timestamp
# normalizes the FORMAT (23 bytes either way) but cannot reconcile two
# clocks, so the divergence is a same-length substitution ~1051 bytes into
# this template — invisible to any byte-count diagnostic and fatal to the
# prefix. Relocating it removes the LAST timestamp from the stable half, so
# the clock-source question stops mattering for caching at all (the model
# still sees creation time every turn, in the turn block).
# Description and actors stay: the updater never touches description, and
# actor changes are structural membership events (a legal one-time cache
# break). Everything left is constant for the lifetime of a CLI session
# (narrative switch = new session), so this half can live in the cacheable
# system-prompt prefix.
# Any edit to the shared wording must be applied to BOTH templates —
# tests/narrative/test_narrative_prompt_split.py locks the equivalence.
# ============================================================================
NARRATIVE_STABLE_PROMPT_TEMPLATE = """
## Narrative System (Common Knowledge)

### What is a Narrative?
A Narrative is a context container for conversations/tasks, used for:
- Organizing related conversation history and task progress
- Maintaining participant (Actors) relationships
- Supporting cross-session continuity tracking

### Actor Types
| Type | Description | Permissions |
|------|-------------|-------------|
| **USER** | Creator/Owner of the Narrative | Full access, can create Jobs |
| **AGENT** | Participating AI Agent | Assists in executing tasks |
| **PARTICIPANT** | Target user of a Job | Can access this Narrative, but is not the creator |

### System Behavior
- When a user initiates a conversation, the system automatically matches or creates a Narrative
- When creating a Job, the target user (related_entity_id) is added as a PARTICIPANT
- When a PARTICIPANT converses with the Agent, the system loads the associated Narrative context

---

## Current Narrative Info

### Basic Metadata
- Narrative ID: {narrative_id}
- Narrative Type: {type_prompt}

### Narrative Details
- Description: {description}

### Actors (Participants)
{actor_prompt}

### Context Guidelines
1. Your reasoning, decisions, and actions must align with the narrative context at all times.
2. When interpreting user requests, prioritize consistency with the narrative's goals.
3. Use the narrative to maintain continuity across turns.
4. If the narrative contains ambiguities, resolve them through explicit reasoning.
5. Treat the narrative as persistent memory for this task environment.
"""

# ============================================================================
# Narrative TURN prompt template (R4 turn-context relocation)
# Used in PromptBuilder.build_turn_prompt(); rendered into the [Turn context]
# block of the current user message. Carries exactly the four fields removed
# from the stable template: updated_at changes every turn, name and
# current_summary are LLM-regenerated on every narrative update (name added
# in R4c), created_at has two clock sources so its rendered value can differ
# between the creating round and every later round (added in R4d — see the
# stable-template comment above for both).
# Relocated, not dropped — the model still sees all four every turn.
# ============================================================================
NARRATIVE_TURN_PROMPT_TEMPLATE = """## Current narrative state

- Name: {name}
- Created: {created_at}
- Last updated: {updated_at}
- Current summary: {current_summary}
"""
