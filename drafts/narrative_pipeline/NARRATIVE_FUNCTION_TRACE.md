# Narrative Pipeline — Exhaustive Function Trace

> Last updated: 2026-03-31
> Auto-generated from codebase analysis. All line numbers reference the main branch.

## EXHAUSTIVE FUNCTION-LEVEL TRACE OF NARRATIVE RETRIEVAL AND UPDATE PIPELINE

---

### **SELECTION CHAIN**

#### **1. NarrativeService.select()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/narrative/narrative_service.py`
**Lines:** 112-270

**Signature:**
```python
async def select(
    self,
    agent_id: str,
    user_id: str,
    input_content: str,
    max_narratives: Optional[int] = None,
    session: Optional[ConversationSession] = None,
    awareness: Optional[str] = None,
) -> NarrativeSelectionResult
```

**Steps:**
1. Load `config.MAX_NARRATIVES_IN_CONTEXT` (default max_narratives)
2. If `session.last_query` exists, instantiate `ContinuityDetector()` (lazy loaded)
3. Call `detector.detect()` with current_query, session, current_narrative, awareness
4. Generate query embedding via `get_embedding(input_content)`
5. **If continuity detection passes:**
   - Load current narrative via `self._crud.load_by_id(session.current_narrative_id)`
   - Call `retrieve_top_k_by_embedding()` with top_k=max_narratives+1
   - Load narratives from search results, prioritize main narrative in first position
   - If main_narrative not in results, force add to position 0, truncate to max_narratives
6. **If not continuous or no narratives returned:**
   - Call `self._retrieval.retrieve_top_k()` with full pipeline
7. Update session if narratives exist:
   - `session.last_query = input_content`
   - `session.last_query_embedding = query_embedding`
   - `session.current_narrative_id = narratives[0].id`
   - `session.query_count += 1`
   - `session.last_query_time = datetime.now(timezone.utc)`
8. Return `NarrativeSelectionResult` with narratives, query_embedding, selection_reason, selection_method, retrieval_method, evermemos_memories

**Returns:** `NarrativeSelectionResult`

**External Calls:**
- `get_embedding(input_content)` → embedding API
- `ContinuityDetector.detect()` → LLM call
- `self._crud.load_by_id()` → database load
- `retrieve_top_k_by_embedding()` → vector search
- `retrieve_top_k()` → full retrieval pipeline

---

#### **2. NarrativeService.combine_main_narrative_prompt()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/narrative/narrative_service.py`
**Lines:** 378-380

**Signature:**
```python
async def combine_main_narrative_prompt(self, narrative: Narrative) -> str
```

**Steps:**
1. Delegates to `PromptBuilder.build_main_prompt(narrative)`

**Returns:** `str` (formatted narrative prompt)

**External Calls:**
- `PromptBuilder.build_main_prompt(narrative)` → prompt generation

---

#### **3. ContinuityDetector.detect()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/narrative/_narrative_impl/continuity.py`
**Lines:** 143-198

**Signature:**
```python
async def detect(
    self,
    current_query: str,
    session: ConversationSession,
    current_narrative: Optional["Narrative"] = None,
    awareness: Optional[str] = None
) -> ContinuityResult
```

**Steps:**
1. Check if `session.last_query` exists and is not empty
2. If no previous query, return `ContinuityResult(is_continuous=False, confidence=1.0, reason="new_session")`
3. Calculate time elapsed: `(datetime.now(timezone.utc) - session.last_query_time).total_seconds()`
4. Convert to minutes: `time_elapsed / 60.0`
5. Call `self._call_llm()` with previous_query, previous_response, current_query, time_elapsed_minutes, current_narrative, awareness
6. Handle exceptions, return `ContinuityResult(is_continuous=False, confidence=0.5, reason="llm_error")`

**Returns:** `ContinuityResult(is_continuous: bool, confidence: float, reason: str)`

**External Calls:**
- `_call_llm()` → LLM judgment

---

#### **4. ContinuityDetector._call_llm()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/narrative/_narrative_impl/continuity.py`
**Lines:** 200-280

**Signature:**
```python
async def _call_llm(
    self,
    previous_query: str,
    previous_response: str,
    current_query: str,
    time_elapsed_minutes: float,
    current_narrative: Optional["Narrative"] = None,
    awareness: Optional[str] = None
) -> ContinuityResult
```

**Steps:**
1. Load `CONTINUITY_DETECTION_INSTRUCTIONS` prompt template
2. Build narrative context:
   - Check if `current_narrative.is_special == "default"` (special default narratives have strict boundaries)
   - Include name, description, current_summary, topic_keywords
3. Strip Matrix channel template from queries via `_extract_core_content()` (regex parsing of Matrix protocol format)
4. Build awareness context if provided
5. Construct user input with previous conversation turn, narrative info, awareness, current query, time elapsed
6. Call `OpenAIAgentsSDK().llm_function()` with:
   - `instructions=CONTINUITY_DETECTION_INSTRUCTIONS`
   - `user_input=formatted_prompt`
   - `output_type=ContinuityOutput`
   - `model=narrative_config.CONTINUITY_LLM_MODEL`
7. Clamp confidence to [0.0, 1.0]
8. Return `ContinuityResult` with LLM decision

**Returns:** `ContinuityResult`

**External Calls:**
- `OpenAIAgentsSDK().llm_function()` → Claude API call
- Output schema: `ContinuityOutput(is_continuous: bool, confidence: float [0-1], reason: str)`

**Config Parameters Used:**
- `narrative_config.CONTINUITY_LLM_MODEL` (default: Claude model)

---

### **RETRIEVAL CHAIN**

#### **5. NarrativeRetrieval.retrieve_top_k()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/narrative/_narrative_impl/retrieval.py`
**Lines:** 169-380

**Signature:**
```python
async def retrieve_top_k(
    self,
    query: str,
    user_id: str,
    agent_id: str,
    top_k: int,
    narrative_type: NarrativeType = NarrativeType.CHAT
) -> NarrativeSelectionResult
```

**Steps:**
1. Call `_ensure_default_narratives(agent_id, user_id)` to create 8 default narratives if missing
2. Call `_get_participant_narratives(user_id, agent_id)` to get narratives where user is a PARTICIPANT
3. Generate query embedding: `await get_embedding(query)`
4. Call `_search(query_embedding, user_id, agent_id, top_k=max(top_k*2, NARRATIVE_SEARCH_TOP_K), query_text=query)`
5. Add PARTICIPANT narratives to search results (calculate similarity if missing embedding)
6. Re-sort by similarity score and update ranks
7. Call `_enhance_with_events()` if retrieval method != "evermemos"
8. Build evermemos_memories cache for each result with episode_summaries and episode_contents
9. **Two-tier threshold judgment:**
   - **High confidence** (best_score >= NARRATIVE_MATCH_HIGH_THRESHOLD and no PARTICIPANT narratives): Return top_k directly
   - **Low confidence or PARTICIPANT narratives present**: Call `_llm_unified_match()`
   - **LLM disabled**: Create new narrative directly

**Returns:** `NarrativeSelectionResult(narratives, query_embedding, selection_reason, selection_method, is_new, best_score, retrieval_method, evermemos_memories)`

**External Calls:**
- `_ensure_default_narratives()` → create defaults
- `_get_participant_narratives()` → query by participant
- `get_embedding(query)` → embedding API
- `_search()` → vector/EverMemOS search
- `_enhance_with_events()` → event-based scoring
- `_llm_unified_match()` → LLM judgment
- `_create_with_embedding()` → create new narrative

**Config Parameters Used:**
- `config.NARRATIVE_SEARCH_TOP_K` (multiplier)
- `config.NARRATIVE_MATCH_HIGH_THRESHOLD` (score threshold, e.g., 0.8)
- `config.NARRATIVE_MATCH_USE_LLM` (enable LLM judgment)
- `config.EVERMEMOS_ENABLED` (EverMemOS mode)

---

#### **6. NarrativeRetrieval.retrieve_or_create()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/narrative/_narrative_impl/retrieval.py`
**Lines:** 87-157

**Signature:**
```python
async def retrieve_or_create(
    self,
    query: str,
    user_id: str,
    agent_id: str,
    narrative_type: NarrativeType = NarrativeType.CHAT
) -> Tuple[Narrative, bool]
```

**Steps:**
1. Generate query embedding: `await get_embedding(query)`
2. Call `_search(query_embedding, user_id, agent_id, top_k=NARRATIVE_SEARCH_TOP_K, query_text=query)`
3. Call `_enhance_with_events()` on search results
4. Evaluate best_match:
   - **High confidence** (>= NARRATIVE_MATCH_HIGH_THRESHOLD): Load and return narrative
   - **Low confidence** (< NARRATIVE_MATCH_LOW_THRESHOLD): Create new narrative
   - **Middle range with LLM enabled**: Prepare candidates, call `_llm_confirm()`, load if matched
   - **Middle range without LLM but >= NARRATIVE_MATCH_THRESHOLD**: Load and return
5. Create new narrative with `_create_with_embedding()`

**Returns:** `Tuple[Narrative, bool]` where bool indicates if newly created

**External Calls:**
- `get_embedding()` → embedding API
- `_search()` → vector search
- `_enhance_with_events()` → event scoring
- `_llm_confirm()` → LLM confirmation
- `_crud.load_by_id()` → load narrative
- `_create_with_embedding()` → create new narrative

**Config Parameters Used:**
- `config.NARRATIVE_SEARCH_TOP_K`
- `config.NARRATIVE_MATCH_HIGH_THRESHOLD`
- `config.NARRATIVE_MATCH_LOW_THRESHOLD`
- `config.NARRATIVE_MATCH_THRESHOLD`
- `config.NARRATIVE_MATCH_USE_LLM`

---

#### **7. NarrativeRetrieval.retrieve_top_k_by_embedding()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/narrative/_narrative_impl/retrieval.py`
**Lines:** 424-449

**Signature:**
```python
async def retrieve_top_k_by_embedding(
    self,
    query_embedding: List[float],
    user_id: str,
    agent_id: str,
    top_k: int
) -> List[NarrativeSearchResult]
```

**Steps:**
1. Call `_search(query_embedding, user_id, agent_id, top_k)`
2. Call `_enhance_with_events()` on results
3. Return top_k results

**Returns:** `List[NarrativeSearchResult]`

**External Calls:**
- `_search()` → vector search
- `_enhance_with_events()` → event scoring

---

#### **8. NarrativeRetrieval.retrieve_auxiliary_narratives()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/narrative/_narrative_impl/retrieval.py`
**Lines:** 382-409

**Signature:**
```python
async def retrieve_auxiliary_narratives(
    self,
    query_embedding: List[float],
    user_id: str,
    agent_id: str,
    exclude_narrative_ids: List[str],
    top_k: int
) -> List[Narrative]
```

**Steps:**
1. Call `_search(query_embedding, user_id, agent_id, top_k=top_k*2)`
2. Call `_enhance_with_events()` on results
3. Load narratives from results, excluding specified IDs
4. Return up to top_k narrative objects

**Returns:** `List[Narrative]`

**External Calls:**
- `_search()` → vector search
- `_enhance_with_events()` → event scoring
- `_crud.load_by_id()` → load full narrative objects

---

#### **9. NarrativeRetrieval._search()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/narrative/_narrative_impl/retrieval.py`
**Lines:** 500-563

**Signature:**
```python
async def _search(
    self,
    query_embedding: List[float],
    user_id: str,
    agent_id: str,
    top_k: int,
    query_text: str = ""
) -> Tuple[List[NarrativeSearchResult], str]
```

**Steps:**
1. **If EverMemOS mode enabled (config.EVERMEMOS_ENABLED):**
   - Call `get_evermemos_client(agent_id, user_id)`
   - Query agent's narrative IDs via `NarrativeRepository.get_by_agent(agent_id)` for agent isolation
   - Call `evermemos.search_narratives(query_text, top_k, agent_narrative_ids)`
   - If results returned, return with method="evermemos"
   - If empty results, log fallback message
   - On exception, log and fall through to native retrieval

2. **Native vector retrieval (default/fallback):**
   - Set filters: `{"user_id": user_id, "agent_id": agent_id}`
   - Call `self._vector_store.search(query_embedding, filters, top_k, min_score=VECTOR_SEARCH_MIN_SCORE, db_client)`
   - Return results with method="vector" or "fallback_vector"

**Returns:** `Tuple[List[NarrativeSearchResult], str]` (results, retrieval_method)

**External Calls:**
- `get_evermemos_client()` → EverMemOS client
- `evermemos.search_narratives()` → HTTP GET /api/v1/memories/search
- `NarrativeRepository.get_by_agent()` → database query for agent isolation
- `VectorStore.search()` → in-memory cosine similarity search
- `get_db_client()` → database connection

**Config Parameters Used:**
- `config.EVERMEMOS_ENABLED` (enable EverMemOS mode)
- `config.VECTOR_SEARCH_MIN_SCORE` (minimum similarity threshold)

---

#### **10. NarrativeRetrieval._enhance_with_events()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/narrative/_narrative_impl/retrieval.py`
**Lines:** 565-611

**Signature:**
```python
async def _enhance_with_events(
    self,
    search_results: List[NarrativeSearchResult],
    query_embedding: List[float]
) -> List[NarrativeSearchResult]
```

**Steps:**
1. For each search_result:
   - Load narrative from database
   - If narrative has event_ids:
     - Get recent events: `narrative.event_ids[-config.MATCH_RECENT_EVENTS_COUNT:]`
     - Load events from event_service
     - For each event, get embedding of event.env_context["input"]
     - Compute average embedding: `compute_average_embedding(event_embeddings)`
     - Calculate events_score: `cosine_similarity(query_embedding, avg_embedding)`
     - Blend scores: `final_score = topic_score * (1 - RECENT_EVENTS_WEIGHT) + events_score * RECENT_EVENTS_WEIGHT`
2. Re-sort by similarity descending
3. Update ranks (1-based)

**Returns:** `List[NarrativeSearchResult]` (enhanced and re-ranked)

**External Calls:**
- `_crud.load_by_id()` → load narrative
- `_event_service.load_events_from_db()` → load events
- `get_embedding()` → embedding API for event inputs
- `compute_average_embedding()` → utility function
- `cosine_similarity()` → utility function

**Config Parameters Used:**
- `config.RECENT_EVENTS_WEIGHT` (blending weight, e.g., 0.3)
- `config.MATCH_RECENT_EVENTS_COUNT` (max recent events to consider)

---

#### **11. NarrativeRetrieval._llm_unified_match()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/narrative/_narrative_impl/retrieval.py`
**Lines:** 613-820+ (method definition spans ~200 lines)

**Signature:**
```python
async def _llm_unified_match(
    self,
    query: str,
    search_results: List[NarrativeSearchResult],
    agent_id: str,
    user_id: str,
    top_k: int,
    query_embedding: List[float],
    narrative_type: NarrativeType,
    best_score: Optional[float],
    participant_narratives: List[Narrative],
    retrieval_method: str
) -> NarrativeSelectionResult
```

**Steps:**
1. Initialize search_candidates, evermemos_memories dicts
2. **Prepare search_candidates** from search_results:
   - Load each narrative from database
   - Extract episode_summaries and episode_contents (Phase 1 & 4)
   - Build matched_content from summaries (truncate to 500 chars)
   - Store in evermemos_memories cache
   - Use narrative.narrative_info.name/current_summary for candidate description (fallback to topic_hint)
   - Log [Phase 1] matched_content info
3. **Get default_candidates** from Repository:
   - Query `NarrativeRepository.get_default_narratives(agent_id, user_id)`
   - For each default narrative, lookup config to get examples
4. **Prepare participant_candidates** (P0-4):
   - For each participant_narrative, extract topic_hint as name/description
   - Log P0-4 candidate count
5. **Call `_llm_judge_unified()`** with search_candidates, default_candidates, participant_candidates
6. **Parse LLM result** (matched_id, matched_type, reason):
   - **If matched_type == "default"**: Load matched narrative, return as single-item list
   - **If matched_type == "participant"**: Load matched narrative, return as single-item list
   - **If matched_type == "search"**: Load matched narrative + other top-k results (excluding matched)
   - **If matched_id is None**: Create new narrative
7. Return `NarrativeSelectionResult` with appropriate selection_method

**Returns:** `NarrativeSelectionResult`

**External Calls:**
- `_crud.load_by_id()` → load narratives for candidates
- `NarrativeRepository.get_default_narratives()` → database query
- `_llm_judge_unified()` → LLM judgment
- `_create_with_embedding()` → create new narrative if no match

**Config Parameters Used:**
- `DEFAULT_NARRATIVES_CONFIG` (hardcoded list of default narrative configs)
- `config.EVERMEMOS_EPISODE_SUMMARIES_PER_NARRATIVE` (max summaries to extract)
- `config.EVERMEMOS_EPISODE_CONTENTS_PER_NARRATIVE` (max contents to extract)

---

#### **12. NarrativeRetrieval._prepare_candidates()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/narrative/_narrative_impl/retrieval.py`
**Lines:** 822-835

**Signature:**
```python
async def _prepare_candidates(
    self,
    search_results: List[NarrativeSearchResult]
) -> List[dict]
```

**Steps:**
1. For each search result:
   - Load narrative by ID
   - Extract name from topic_hint (first 30 chars)
   - Extract query from topic_hint (first 50 chars)
   - Append `{"id", "name", "query"}` to candidates list

**Returns:** `List[dict]` with `{id: str, name: str, query: str}`

**External Calls:**
- `_crud.load_by_id()` → load narrative

---

#### **13. NarrativeRetrieval._get_participant_narratives()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/narrative/_narrative_impl/retrieval.py`
**Lines:** 868-912

**Signature:**
```python
async def _get_participant_narratives(
    self,
    user_id: str,
    agent_id: str
) -> List[Narrative]
```

**Steps:**
1. Create `NarrativeRepository(db_client)` instance
2. Call `repo.get_narratives_by_participant(user_id, agent_id)`
3. Log result count
4. Catch exceptions, return empty list

**Returns:** `List[Narrative]` (all narratives where user is a PARTICIPANT actor)

**External Calls:**
- `get_db_client()` → database connection
- `NarrativeRepository.get_narratives_by_participant()` → SQL query for narratives with PARTICIPANT actor

---

#### **14. NarrativeRetrieval._create_with_embedding()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/narrative/_narrative_impl/retrieval.py`
**Lines:** 914-957

**Signature:**
```python
async def _create_with_embedding(
    self,
    query: str,
    query_embedding: List[float],
    user_id: str,
    agent_id: str,
    narrative_type: NarrativeType
) -> Narrative
```

**Steps:**
1. Extract keywords from query: `extract_keywords(query)`
2. Generate topic_hint: `truncate_text(query, config.SUMMARY_MAX_LENGTH)`
3. Generate title: `truncate_text(query, 30)`
4. Create narrative via CRUD: `self._crud.create(agent_id, user_id, narrative_type, title, description)`
5. Set routing index fields:
   - `narrative.topic_keywords = topic_keywords`
   - `narrative.topic_hint = topic_hint`
   - `narrative.routing_embedding = query_embedding`
   - `narrative.embedding_updated_at = datetime.now(timezone.utc)`
   - `narrative.events_since_last_embedding_update = 0`
6. Save narrative to database
7. Add to VectorStore: `self._vector_store.add(narrative_id, query_embedding, metadata)`

**Returns:** `Narrative` (newly created)

**External Calls:**
- `extract_keywords()` → keyword extraction utility
- `truncate_text()` → text truncation utility
- `_crud.create()` → database insert
- `_crud.save()` → database update
- `_vector_store.add()` → in-memory vector store

**Config Parameters Used:**
- `config.SUMMARY_MAX_LENGTH` (topic_hint max length, e.g., 500)

---

#### **15. NarrativeRetrieval._ensure_default_narratives()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/narrative/_narrative_impl/retrieval.py`
**Lines:** 451-484

**Signature:**
```python
async def _ensure_default_narratives(self, agent_id: str, user_id: str) -> None
```

**Steps:**
1. Create `NarrativeRepository(db_client)` instance
2. Query count of default narratives: `repo.count_default_narratives(agent_id, user_id)`
3. If count > 0, log and return
4. If count == 0:
   - Call `ensure_default_narratives(agent_id, user_id, crud=self._crud)`
   - Log success or exception
   - Catch and log exception without raising (allow main flow to continue)

**Returns:** None

**External Calls:**
- `get_db_client()` → database connection
- `NarrativeRepository.count_default_narratives()` → SQL count query
- `ensure_default_narratives()` → creates 8 default narratives (Greeting, Task, Question, Info, Event, Feedback, Other, Clarification)

---

### **LLM FUNCTIONS**

#### **16. llm_confirm()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/narrative/_narrative_impl/_retrieval_llm.py`
**Lines:** 61-104

**Signature:**
```python
async def llm_confirm(query: str, candidates: List[dict]) -> dict
```

**Steps:**
1. Return `{"matched_id": None, "reason": "No candidates"}` if no candidates
2. Build user_input: iterate candidates, append "{index}: {name}\nDescription: {query}\n\n"
3. Create `OpenAIAgentsSDK()` instance
4. Call `sdk.llm_function()` with:
   - `instructions=NARRATIVE_SINGLE_MATCH_INSTRUCTIONS`
   - `user_input=formatted_list + "User's new query: {query}"`
   - `output_type=NarrativeMatchOutput`
   - `model=config.NARRATIVE_JUDGE_LLM_MODEL`
5. Extract `output.final_output` from RunResult
6. **If matched_index is valid and relation_type in (CONTINUATION, REFERENCE):**
   - Return `{"matched_id": candidates[matched_index]["id"], "reason": output.reason}`
7. **Otherwise:** Return `{"matched_id": None, "reason": output.reason or "New topic"}`

**Returns:** `dict` with `{matched_id: str/None, reason: str}`

**External Calls:**
- `OpenAIAgentsSDK().llm_function()` → Claude API call
- Output schema: `NarrativeMatchOutput(reason: str, matched_index: int, relation_type: RelationType)`

**Config Parameters Used:**
- `config.NARRATIVE_JUDGE_LLM_MODEL` (Claude model)

---

#### **17. llm_judge_unified()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/narrative/_narrative_impl/_retrieval_llm.py`
**Lines:** 107-239

**Signature:**
```python
async def llm_judge_unified(
    query: str,
    search_candidates: List[dict],
    default_candidates: List[dict],
    participant_candidates: Optional[List[dict]] = None,
) -> dict
```

**Steps:**
1. Return early if no candidates of any type
2. Check if `participant_candidates` has length > 0
3. **Select instructions** based on participant context:
   - If participant_candidates present: `NARRATIVE_UNIFIED_MATCH_WITH_PARTICIPANT_INSTRUCTIONS`
   - Otherwise: `NARRATIVE_UNIFIED_MATCH_INSTRUCTIONS`
4. **Build user_input** string:
   - **[Participant-i]** section (if present): name, description
   - **[Default-i]** section: name, description, examples (up to 3)
   - **[Topic-i]** section (search results): name, description, similarity_score, matched_content (Phase 1)
   - **User's New Query** section
5. Create `OpenAIAgentsSDK()` instance
6. Call `sdk.llm_function()` with:
   - `instructions=selected_instructions`
   - `user_input=formatted_candidates`
   - `output_type=UnifiedMatchOutput`
   - `model=config.NARRATIVE_JUDGE_LLM_MODEL`
7. Extract `output.final_output`
8. **Parse matched_category and matched_index:**
   - **"participant"**: Validate index, return with matched_type="participant"
   - **"default"**: Validate index, return with matched_type="default"
   - **"search"**: Validate index, return with matched_type="search"
   - **"none" or error**: Log and return `{"matched_id": None, "matched_type": None, "reason": ...}`

**Returns:** `dict` with `{matched_id: str/None, matched_type: str/None, reason: str}`

**External Calls:**
- `OpenAIAgentsSDK().llm_function()` → Claude API call
- Output schema: `UnifiedMatchOutput(reason: str, matched_category: str, matched_index: int)`

**Config Parameters Used:**
- `config.NARRATIVE_JUDGE_LLM_MODEL` (Claude model)

---

### **VECTOR STORE**

#### **18. VectorStore.search()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/narrative/_narrative_impl/vector_store.py`
**Lines:** 105-166

**Signature:**
```python
async def search(
    self,
    query_embedding: List[float],
    filters: Optional[Dict[str, str]] = None,
    top_k: int = 3,
    min_score: float = 0.0,
    db_client=None
) -> List[NarrativeSearchResult]
```

**Steps:**
1. **On-demand loading:** If `_embeddings` is empty and db_client provided:
   - Call `load_from_db(db_client, agent_id, user_id)`
2. **Filter candidates** by metadata (user_id, agent_id):
   - Iterate `_embeddings`, apply filter conditions
   - Build candidates list: `[(narrative_id, embedding), ...]`
3. **Calculate cosine similarity** for each candidate:
   - Call `_cosine_similarity(query_embedding, embedding)`
   - Keep if score >= min_score
4. **Sort by score descending**, take top_k
5. **Build NarrativeSearchResult** for each:
   - `NarrativeSearchResult(narrative_id, similarity_score, rank=i+1)`

**Returns:** `List[NarrativeSearchResult]` (sorted by score, descending)

**Config Parameters Used:**
- `config.VECTOR_SEARCH_MIN_SCORE` (passed as min_score)

---

#### **19. VectorStore.load_from_db()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/narrative/_narrative_impl/vector_store.py`
**Lines:** 49-93

**Signature:**
```python
async def load_from_db(
    self,
    db_client: "DatabaseClient",
    agent_id: str,
    user_id: Optional[str] = None
) -> int
```

**Steps:**
1. Create filter_key: `(agent_id, user_id or "")`
2. If already loaded (in `_loaded_filters`), return 0
3. Create `NarrativeRepository(db_client)` instance
4. Query narratives: `narrative_repo.get_with_embedding(agent_id, user_id, limit=1000)`
5. For each narrative with non-null routing_embedding:
   - Store in `_embeddings[narrative.id] = narrative.routing_embedding`
   - Store in `_metadata[narrative.id] = {"agent_id": agent_id, "user_id": user_id or ""}`
   - Increment loaded_count
6. Add filter_key to `_loaded_filters`

**Returns:** `int` (count of loaded embeddings)

**External Calls:**
- `NarrativeRepository.get_with_embedding()` → database query (limit 1000)

---

#### **20. VectorStore._cosine_similarity()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/narrative/_narrative_impl/vector_store.py`
**Lines:** 188-196

**Signature:**
```python
def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float
```

**Steps:**
1. If numpy available:
   - Convert to numpy arrays
   - Calculate: `np.dot(v1, v2)` (dot product)
   - Convert to float
2. Else (fallback):
   - Calculate: `sum(a * b for a, b in zip(vec1, vec2))`
3. Clamp result to [0.0, 1.0]: `max(0.0, min(1.0, similarity))`

**Returns:** `float` (cosine similarity in [0, 1])

---

### **EVERMEMOS CLIENT**

#### **21. EverMemOSClient.search_narratives()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/utils/evermemos/client.py`
**Lines:** 282-362

**Signature:**
```python
async def search_narratives(
    self,
    query: str,
    top_k: int = 10,
    agent_narrative_ids: Optional[set] = None
) -> List["NarrativeSearchResult"]
```

**Steps:**
1. Build params:
   - `query=query`
   - `top_k=top_k*3` (fetch more, may reduce after aggregation)
   - `memory_types="episodic_memory"`
   - `retrieve_method="rrf"` (BM25 + Vector + RRF fusion)
   - `user_id=self.user_id`
2. Call HTTP GET with httpx: `client.get(self.search_url, params=params, timeout=self.timeout)`
3. Check response status (must be 200)
4. Parse JSON: `result.get("result", {})`
5. Extract:
   - `raw_memories = result["memories"]` (list of {group_id: [episodes]})
   - `raw_scores = result["scores"]` (list of {group_id: [scores]})
   - `pending_messages = result["pending_messages"]` (raw messages)
6. Call `_filter_pending_messages_by_agent(pending_messages, agent_narrative_ids)`
7. Call `_aggregate_by_narrative(raw_memories, raw_scores, top_k, allowed_groups, agent_narrative_ids)`

**Returns:** `List[NarrativeSearchResult]` with episode_summaries and episode_contents

**External Calls:**
- `httpx.AsyncClient().get()` → HTTP GET /api/v1/memories/search
- `_filter_pending_messages_by_agent()` → filter by agent
- `_aggregate_by_narrative()` → aggregate and score

**Config Parameters Used:**
- `self.timeout` (default 30s from EVERMEMOS_TIMEOUT env)
- `self.search_url` (HTTP endpoint: {base_url}/api/v1/memories/search)

**EverMemOS API:**
- **Endpoint:** GET /api/v1/memories/search
- **Parameters:**
  - query (string)
  - top_k (int)
  - memory_types (string: "episodic_memory")
  - retrieve_method (string: "rrf" - Reciprocal Rank Fusion)
  - user_id (string)
- **Response:** `{status, result: {memories, scores, pending_messages}}`

---

#### **22. EverMemOSClient._filter_pending_messages_by_agent()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/utils/evermemos/client.py`
**Lines:** 364-422

**Signature:**
```python
def _filter_pending_messages_by_agent(
    self,
    pending_messages: List[Dict],
    agent_narrative_ids: Optional[set] = None
) -> Optional[set]
```

**Steps:**
1. If no pending_messages, return None
2. Initialize allowed_groups, all_groups (empty sets)
3. For each message in pending_messages:
   - Extract group_id
   - Add to all_groups
   - If group_id in agent_narrative_ids, add to allowed_groups
4. Log filtering results (count filtered out)
5. Return allowed_groups (or empty set if no matches)

**Returns:** `Optional[set]` of group_ids belonging to current agent

**Purpose:** Agent isolation for pending_messages (not yet processed into episodic_memory)

---

#### **23. EverMemOSClient._aggregate_by_narrative()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/utils/evermemos/client.py`
**Lines:** 424-666

**Signature:**
```python
def _aggregate_by_narrative(
    self,
    raw_memories: List[Dict],
    raw_scores: List[Dict],
    top_k: int,
    allowed_groups: Optional[set] = None,
    agent_narrative_ids: Optional[set] = None
) -> List["NarrativeSearchResult"]
```

**Steps:**
1. Initialize narrative_scores, narrative_summaries, narrative_contents dicts
2. **Extract scores** from raw_scores:
   - For each {group_id: [scores]} dict:
     - Take max score for group
     - Store in narrative_scores[group_id]
3. **Extract episode summaries and contents** from raw_memories:
   - For each {group_id: [episodes]} dict:
     - **Agent isolation check:** If agent_narrative_ids provided and group_id not in it, skip (add to filtered_groups)
     - For each episode:
       - Extract "episode" field (raw content)
       - Extract "summary" field (or truncate episode to 200 chars if no summary)
       - Store in narrative_summaries[group_id]
     - Store raw episode contents in narrative_contents[group_id]
4. **Agent isolation cleanup:**
   - Remove filtered_groups from narrative_scores
   - Log filtered count
5. **Agent isolation for pending_messages:**
   - If allowed_groups is not None (pending_messages existed):
     - Remove groups not in allowed_groups from narrative_scores
6. **Score normalization (RRF):**
   - If narrative_scores empty but allowed_groups not None:
     - Assign PENDING_ONLY_DEFAULT_SCORE = 0.03 to each allowed group
   - **Proportional mapping:** `scaled_score = raw_score * RRF_SCALE_FACTOR` (SCALE_FACTOR=10)
   - Cap at RRF_MAX_SCORE = 0.95
   - Log mapping range
7. **Sort and build results:**
   - Sort by score descending
   - For each narrative in top_k:
     - Get episode summaries (limited by config.EVERMEMOS_EPISODE_SUMMARIES_PER_NARRATIVE)
     - Get episode contents (limited by config.EVERMEMOS_EPISODE_CONTENTS_PER_NARRATIVE)
     - Build NarrativeSearchResult with rank

**Returns:** `List[NarrativeSearchResult]` with episode_summaries and episode_contents

**Config Parameters Used:**
- `PENDING_ONLY_DEFAULT_SCORE = 0.03` (for pending_messages-only narratives)
- `RRF_SCALE_FACTOR = 10.0` (proportional scaling)
- `RRF_MAX_SCORE = 0.95` (cap on scaled scores)
- `config.EVERMEMOS_EPISODE_SUMMARIES_PER_NARRATIVE` (max summaries per narrative)
- `config.EVERMEMOS_EPISODE_CONTENTS_PER_NARRATIVE` (max contents per narrative)

---

#### **24. EverMemOSClient.write_event()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/utils/evermemos/client.py`
**Lines:** 73-147

**Signature:**
```python
async def write_event(self, event: "Event", narrative: "Narrative") -> bool
```

**Steps:**
1. Call `_ensure_conversation_meta(narrative)`
2. Convert event to messages: `_event_to_messages(event, narrative)`
3. If no messages, return True
4. **For each message:**
   - POST to self.memorize_url (HTTP POST /api/v1/memories)
   - Check response status:
     - 200: Success
     - 202: Accepted (async processing)
     - Other: Log warning
5. Handle exceptions: ConnectError, TimeoutException, general Exception
6. Return success boolean

**Returns:** `bool` (overall write success)

**External Calls:**
- `_ensure_conversation_meta()` → POST /api/v1/memories/conversation-meta
- `_event_to_messages()` → format conversion
- `httpx.AsyncClient().post()` → HTTP POST /api/v1/memories

**EverMemOS API:**
- **Endpoint:** POST /api/v1/memories
- **Body:** Message object with message_id, create_time, sender, role, type, content, group_id, group_name, scene

---

#### **25. EverMemOSClient._ensure_conversation_meta()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/utils/evermemos/client.py`
**Lines:** 149-212

**Signature:**
```python
async def _ensure_conversation_meta(self, narrative: "Narrative") -> bool
```

**Steps:**
1. Get narrative_id, narrative_name (from narrative_info.name or narrative_id)
2. Check cache: `_conversation_meta_saved[narrative_id]` - if True, return True
3. Build payload:
   - version: "1.0"
   - scene: "assistant"
   - scene_desc: {}
   - name: narrative_name
   - description: narrative.narrative_info.description
   - group_id: narrative_id
   - created_at: ISO timestamp
   - default_timezone: "UTC"
   - user_details: {user_id: {full_name, role: "user"}}
   - tags: ["narrative", agent_id]
4. POST to self.conversation_meta_url
5. Check response status (must be 200)
6. Mark cache and return True on success
7. Mark cache True even on failure (to avoid retry attempts)

**Returns:** `bool` (successful creation)

**External Calls:**
- `httpx.AsyncClient().post()` → HTTP POST /api/v1/memories/conversation-meta

**EverMemOS API:**
- **Endpoint:** POST /api/v1/memories/conversation-meta
- **Body:** Conversation metadata object

---

#### **26. EverMemOSClient._event_to_messages()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/utils/evermemos/client.py`
**Lines:** 214-276

**Signature:**
```python
def _event_to_messages(self, event: "Event", narrative: "Narrative") -> List[Dict]
```

**Steps:**
1. Initialize messages list
2. Get narrative_id, narrative_name
3. Get timestamp: `event.created_at.isoformat()` (or event.updated_at as fallback)
4. **If event.env_context["input"] exists:**
   - Build user message with:
     - message_id: "{event_id}_user"
     - create_time: timestamp
     - sender: user_id
     - role: "user"
     - type: "text"
     - content: user input
     - group_id: narrative_id
     - group_name: narrative_name
     - scene: "assistant"
5. **If event.final_output exists:**
   - Build assistant message with:
     - message_id: "{event_id}_agent"
     - create_time: event.updated_at (or timestamp)
     - sender: user_id
     - sender_name: agent_id (!)
     - role: "assistant"
     - type: "text"
     - content: final_output
     - group_id, group_name, scene: same as user message

**Returns:** `List[Dict]` (0, 1, or 2 messages depending on event content)

**Notes:**
- User message uses user_id as sender
- Assistant message also uses user_id as sender but agent_id as sender_name (!)
- Both messages share message_id prefix but differ by suffix (_user, _agent)

---

### **UPDATE CHAIN**

#### **27. NarrativeUpdater.update_with_event()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/narrative/_narrative_impl/updater.py`
**Lines:** 119-233

**Signature:**
```python
async def update_with_event(
    self,
    narrative: Narrative,
    event: Event,
    is_main_narrative: bool = True,
    is_default_narrative: bool = False
) -> Narrative
```

**Steps:**
1. **Reload latest narrative** from database to avoid overwriting concurrent modifications:
   - `latest_narrative = await self._crud.load_by_id(narrative.id)`
2. **If is_default_narrative == True:**
   - Add event_id to event_ids list (if not already present)
   - Update timestamp
   - Save and return (skip all other processing)
3. **Non-default narrative path:**
   - Add event_id to event_ids list
   - Increment `events_since_last_embedding_update`
   - If event.final_output exists:
     - Create DynamicSummaryEntry with event_id, truncated summary (200 chars), timestamp
     - Append to dynamic_summary list
   - Update timestamp
   - Save to database
4. **Determine whether to trigger LLM update (async):**
   - If is_main_narrative == True:
     - Check event_count % NARRATIVE_LLM_UPDATE_INTERVAL
     - If divisible, create async task: `_async_llm_update(narrative, event, trigger_embedding_update)`
     - Else if should_update_embedding, create async task: `_async_embedding_update(narrative)`
   - If is_main_narrative == False:
     - Skip LLM update (log TODO for future dedicated update logic)
5. Return updated narrative

**Returns:** `Narrative` (updated)

**External Calls:**
- `_crud.load_by_id()` → database query for latest version
- `_crud.save()` → database update
- `_async_llm_update()` → async task creation (non-blocking)
- `_async_embedding_update()` → async task creation (non-blocking)

**Config Parameters Used:**
- `NARRATIVE_LLM_UPDATE_INTERVAL` (trigger interval, e.g., every 5 events)

---

#### **28. NarrativeUpdater._async_llm_update()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/narrative/_narrative_impl/updater.py`
**Lines:** 238-282

**Signature:**
```python
async def _async_llm_update(
    self,
    narrative: Narrative,
    event: Event,
    trigger_embedding_update: bool = False
) -> None
```

**Steps:**
1. Build context: `_build_update_context(narrative, event)`
2. Call LLM: `_call_llm_for_update(narrative, context)` → NarrativeUpdateOutput
3. If update_output is not None:
   - Apply updates: `_apply_llm_update(narrative, update_output, event)`
   - Log success
   - If trigger_embedding_update:
     - Create async task: `_async_embedding_update(narrative)`
4. Catch exceptions, log error

**Returns:** None

**External Calls:**
- `_build_update_context()` → context building
- `_call_llm_for_update()` → LLM call
- `_apply_llm_update()` → apply changes
- `_async_embedding_update()` → async embedding update

---

#### **29. NarrativeUpdater._build_update_context()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/narrative/_narrative_impl/updater.py`
**Lines:** 284-316

**Signature:**
```python
async def _build_update_context(self, narrative: Narrative, event: Event) -> str
```

**Steps:**
1. Build context_parts list with:
   - Current Narrative info: name, description, current_summary, keywords
   - Recent conversation history (last config.NARRATIVE_LLM_UPDATE_EVENTS_COUNT entries from dynamic_summary)
   - Latest event details: user input (from env_context), agent response (first 500 chars of final_output)
2. Join with newlines

**Returns:** `str` (formatted context)

**Config Parameters Used:**
- `config.NARRATIVE_LLM_UPDATE_EVENTS_COUNT` (how many recent events to include)

---

#### **30. NarrativeUpdater._call_llm_for_update()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/narrative/_narrative_impl/updater.py`
**Lines:** 318-340

**Signature:**
```python
async def _call_llm_for_update(
    self,
    narrative: Narrative,
    context: str
) -> Optional[NarrativeUpdateOutput]
```

**Steps:**
1. Load NARRATIVE_UPDATE_INSTRUCTIONS
2. Create OpenAIAgentsSDK instance
3. Call `sdk.llm_function()` with:
   - `instructions=NARRATIVE_UPDATE_INSTRUCTIONS`
   - `user_input=context`
   - `output_type=NarrativeUpdateOutput`
4. Extract final_output from RunResult
5. Catch exceptions, log error, return None

**Returns:** `Optional[NarrativeUpdateOutput]`

**External Calls:**
- `OpenAIAgentsSDK().llm_function()` → Claude API call
- Output schema: `NarrativeUpdateOutput(name, current_summary, topic_keywords, actors, dynamic_summary_entry)`

---

#### **31. NarrativeUpdater._apply_llm_update()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/narrative/_narrative_impl/updater.py`
**Lines:** 342-382

**Signature:**
```python
async def _apply_llm_update(
    self,
    narrative: Narrative,
    update_output: NarrativeUpdateOutput,
    event: Event
) -> None
```

**Steps:**
1. **Reload latest narrative** from database (avoid lost update issue with concurrent PARTICIPANT modifications)
   - `latest_narrative = await self._crud.load_by_id(narrative.id)`
2. Update LLM-generated fields only (preserve actors/active_instances):
   - `latest_narrative.narrative_info.name = update_output.name`
   - `latest_narrative.narrative_info.current_summary = update_output.current_summary`
   - `latest_narrative.topic_keywords = update_output.topic_keywords`
3. Update last dynamic_summary entry if exists:
   - `latest_narrative.dynamic_summary[-1].summary = update_output.dynamic_summary_entry`
4. Update timestamp
5. Save to database

**Returns:** None

**External Calls:**
- `_crud.load_by_id()` → database query for latest version
- `_crud.save()` → database update

---

#### **32. NarrativeUpdater._async_embedding_update()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/narrative/_narrative_impl/updater.py`
**Lines:** 384-391

**Signature:**
```python
async def _async_embedding_update(self, narrative: Narrative) -> None
```

**Steps:**
1. Call `check_and_update_embedding(narrative)` → bool
2. If updated, log success
3. Catch exceptions, log warning

**Returns:** None

**External Calls:**
- `check_and_update_embedding()` → embedding generation and update

---

#### **33. NarrativeUpdater.check_and_update_embedding()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/narrative/_narrative_impl/updater.py`
**Lines:** 393-435

**Signature:**
```python
async def check_and_update_embedding(self, narrative: Narrative) -> bool
```

**Steps:**
1. Check if update is needed: `_should_update(narrative)`
2. If not needed, return False
3. Regenerate topic_hint: `_regenerate_topic_hint(narrative)` (based on name + current_summary)
4. Generate new embedding: `await get_embedding(new_hint)`
5. Update narrative fields:
   - `narrative.topic_hint = new_hint`
   - `narrative.routing_embedding = new_embedding`
   - `narrative.embedding_updated_at = datetime.now(timezone.utc)`
   - `narrative.events_since_last_embedding_update = 0`
6. Update VectorStore if it exists:
   - `await self._vector_store.update(narrative.id, new_embedding)`
7. Save to database
8. Return True

**Returns:** `bool` (whether update was performed)

**External Calls:**
- `_should_update()` → check update conditions
- `_regenerate_topic_hint()` → topic hint generation
- `get_embedding()` → embedding API
- `_vector_store.update()` → in-memory vector store update
- `_crud.save()` → database update

---

#### **34. NarrativeUpdater._regenerate_topic_hint()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/narrative/_narrative_impl/updater.py`
**Lines:** 443-464

**Signature:**
```python
def _regenerate_topic_hint(self, narrative: Narrative) -> str
```

**Steps:**
1. Get name from `narrative.narrative_info.name or ""`
2. Get summary from `narrative.narrative_info.current_summary or ""`
3. **Build topic_hint:**
   - If both name and summary: `f"{name}: {summary}"`
   - Else if summary: `summary`
   - Else if name: `name`
   - Else: `f"Conversation {narrative.id}"`
4. Truncate to max length: `truncate_text(topic_hint, config.SUMMARY_MAX_LENGTH)`

**Returns:** `str` (new topic_hint)

**Config Parameters Used:**
- `config.SUMMARY_MAX_LENGTH` (max topic_hint length)

---

#### **35. NarrativeUpdater._should_update()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/narrative/_narrative_impl/updater.py`
**Lines:** 466-474

**Signature:**
```python
def _should_update(self, narrative: Narrative) -> bool
```

**Steps:**
1. If `narrative.embedding_updated_at is None`, return True
2. If `narrative.routing_embedding is None`, return True
3. If `narrative.events_since_last_embedding_update >= config.EMBEDDING_UPDATE_INTERVAL`, return True
4. Else return False

**Returns:** `bool` (whether embedding should be updated)

**Config Parameters Used:**
- `config.EMBEDDING_UPDATE_INTERVAL` (trigger threshold, e.g., every 5 events)

---

### **CONTEXT BUILDING**

#### **36. ChatModule.hook_data_gathering()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/module/chat_module/chat_module.py`
**Lines:** 220-370

**Signature:**
```python
async def hook_data_gathering(self, ctx_data: ContextData) -> ContextData
```

**Steps:**
1. **Load long-term memory** (current Narrative's EverMemOS semantically relevant history):
   - Get instance_ids from self.instance_ids or self.instance_id
   - If evermemos_memories in ctx_data (Phase 2):
     - Extract current_narrative_data[current_narrative_id]
     - For each episode_content, create message: `{role: "context", content, meta_data: {memory_type: "long_term", source: "evermemos", narrative_id, topic_hint}}`
     - Log preview (first 500 chars) of each episode
   - Else (fallback):
     - For each instance_id, call `event_memory_module.search_instance_json_format_memory(module_name, instance_id)`
     - Extract messages, mark with memory_type="long_term"
     - Filter out non-chat source messages that aren't assistant role
2. **Limit long-term memory**: Truncate to MAX_LONG_TERM_MESSAGES = 40 messages (20 rounds × 2)
3. **Load short-term memory** (recent cross-Narrative conversations):
   - Call `_load_short_term_memory(module_name, exclude_instance_ids)`
4. **Merge and sort:**
   - Combine long_term_messages + short_term_messages
   - Sort by timestamp ascending
   - Log total counts
5. **Fill into ctx_data.chat_history**

**Returns:** `ContextData` (with chat_history populated)

**External Calls:**
- `event_memory_module.search_instance_json_format_memory()` → query instance memory from database
- `_load_short_term_memory()` → load cross-narrative messages

**Config Parameters Used:**
- `MAX_LONG_TERM_MESSAGES = 40` (20 rounds × 2)
- `SHORT_TERM_MAX_MESSAGES = 15` (max short-term messages)

---

#### **37. ChatModule._load_short_term_memory()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/module/chat_module/chat_module.py`
**Lines:** 372-458

**Signature:**
```python
async def _load_short_term_memory(
    self,
    module_name: str,
    exclude_instance_ids: List[str]
) -> List[Dict[str, Any]]
```

**Steps:**
1. Create `InstanceRepository(db_client)` instance
2. Query other ChatModule instances: `instance_repo.get_chat_instances_by_user(agent_id, user_id, exclude_instance_ids)`
3. For each instance:
   - Call `event_memory_module.search_instance_json_format_memory(module_name, instance_id)`
   - Extract messages
   - For each message, filter out non-chat source non-assistant messages
   - Mark with memory_type="short_term"
   - Add to short_term_messages
4. Sort by timestamp descending (most recent first)
5. Take most recent SHORT_TERM_MAX_MESSAGES = 15
6. Re-sort by timestamp ascending

**Returns:** `List[Dict[str, Any]]` (short-term memory messages)

**External Calls:**
- `get_db_client()` → database connection
- `InstanceRepository.get_chat_instances_by_user()` → query other instances
- `event_memory_module.search_instance_json_format_memory()` → query instance memory

---

#### **38. ContextRuntime.run()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/context_runtime/context_runtime.py`
**Lines:** 80-184

**Signature:**
```python
async def run(
    self,
    narrative_list: List[Narrative],
    active_instances: List,
    input_content: str,
    working_source: Union[WorkingSource, str] = WorkingSource.CHAT,
    query_embedding: Optional[List[float]] = None,
    created_job_ids: Optional[List[str]] = None,
    evermemos_memories: Optional[Dict[str, Any]] = None,
    trigger_extra_data: Optional[Dict[str, Any]] = None,
) -> ContextRuntimeOutput
```

**Steps:**
1. **Step 0: Initialize ContextData**
   - Extract main_narrative_id from narrative_list[0]
   - Create ContextData(agent_id, user_id, input_content, narrative_id=main_narrative_id, ...)
   - Merge trigger_extra_data (e.g., channel_tag)
   - Store narrative_ids list
   - Store created_job_ids
   - Store evermemos_memories cache (Phase 2)

2. **Step 1-1: Extract Narrative data** (Event selection currently disabled)
   - Set messages = [], selected_events = []
   - Note: ChatModule provides chat_history instead

3. **Step 1-2: Gather Module data**
   - Extract module_list from active_instances
   - Call `hook_manager.hook_data_gathering(module_list, ctx_data)`
   - Get messages from ctx_data.chat_history

4. **Step 1-3: Build Module instructions**
   - For each unique module_class in active_instances:
     - Call `build_module_instructions(module, ctx_data)`
     - Append to module_instructions_list

5. **Step 1-4: Build complete System Prompt**
   - Call `build_complete_system_prompt(narrative_list, selected_events, module_instructions_list, ctx_data)`

6. **Step 2: Build input for Agent Framework**
   - Call `build_input_for_framework(messages, system_prompt, active_instances, ctx_data)`
   - Get final messages and mcp_urls

**Returns:** `ContextRuntimeOutput(messages, mcp_urls, ctx_data)`

**External Calls:**
- `hook_manager.hook_data_gathering()` → module data gathering
- `build_module_instructions()` → instruction building
- `build_complete_system_prompt()` → system prompt generation
- `build_input_for_framework()` → framework input building

---

#### **39. ContextRuntime.build_complete_system_prompt()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/context_runtime/context_runtime.py`
**Lines:** 316-460

**Signature:**
```python
async def build_complete_system_prompt(
    self,
    narrative_list: List[Narrative],
    selected_events: List[Event],
    module_instructions_list: List[ModuleInstructions],
    ctx_data: ContextData
) -> str
```

**Steps:**
1. **Part 1: Narrative Info** (main narrative only)
   - Call `narrative_service.combine_main_narrative_prompt(narrative_list[0])`
2. **Part 2: Event History** (currently disabled; ChatModule provides instead)
3. **Part 3: Auxiliary Narratives**
   - Get auxiliary_summaries from ctx_data.extra_data (if extract_narrative_data called)
   - Else extract from narrative_list[1:]
   - Call `_build_auxiliary_narratives_prompt(auxiliary_summaries, evermemos_memories)`
   - Includes Related Content from evermemos_memories cache if available
4. **Part 4: Module Instructions**
   - Call `_build_module_instructions_prompt(module_instructions_list)`
5. **Part 5: Bootstrap Injection**
   - Check if user is agent creator
   - If Bootstrap.md exists and event_count < 3, inject BOOTSTRAP_INJECTION_PROMPT
   - Set ctx_data.bootstrap_active = True
6. **Combine all parts** with "\n\n" separator

**Returns:** `str` (complete system prompt)

**External Calls:**
- `narrative_service.combine_main_narrative_prompt()` → narrative prompt
- `_build_auxiliary_narratives_prompt()` → auxiliary narrative prompt
- `_build_module_instructions_prompt()` → module instructions prompt
- `AgentRepository.get_agent()` → fetch agent record for creator check

---

#### **40. ContextRuntime.build_input_for_framework()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/context_runtime/context_runtime.py`
**Lines:** 536-643

**Signature:**
```python
async def build_input_for_framework(
    self,
    messages: List[Dict[str, Any]],
    system_prompt: str,
    active_instances: List,
    ctx_data: ContextData
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]
```

**Steps:**
1. Get chat_history from ctx_data (or fall back to messages parameter)
2. **Separate long-term and short-term memory:**
   - long_term_messages: messages with memory_type != "short_term"
   - short_term_messages: messages with memory_type == "short_term"
3. **Truncate long-term messages** (max SINGLE_MESSAGE_MAX_CHARS = 4000 per message)
4. **Build enhanced system prompt:**
   - If short_term_messages exist:
     - Call `_build_short_term_memory_prompt(short_term_messages)`
     - Append to system_prompt
5. **Build final messages list:**
   - Start with system message
   - Add long_term_messages (stripped to role + content only)
   - Add current user input
6. **Collect MCP URLs:**
   - For each unique module_class in active_instances:
     - Call `module.get_mcp_config()`
     - Extract server_url

**Returns:** `Tuple[List[Dict[str, Any]], Dict[str, str]]` (messages, mcp_urls)

**External Calls:**
- `_truncate_long_term_messages()` → truncate per-message content
- `_build_short_term_memory_prompt()` → short-term memory section
- `module.get_mcp_config()` → MCP configuration

**Config Parameters Used:**
- `SINGLE_MESSAGE_MAX_CHARS = 4000` (max per message)
- `SHORT_TERM_TOKEN_LIMIT = 40000` (max short-term memory section)

---

#### **41. ContextRuntime._build_short_term_memory_prompt()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/context_runtime/context_runtime.py`
**Lines:** 649-747

**Signature:**
```python
def _build_short_term_memory_prompt(
    self,
    short_term_messages: List[Dict[str, Any]]
) -> str
```

**Steps:**
1. Build prompt string starting with SHORT_TERM_MEMORY_HEADER
2. Group messages by instance_id (preserving insertion order)
3. Reverse order (most recent first for processing)
4. Initialize budget = SHORT_TERM_TOKEN_LIMIT - len(prompt)
5. For each group (instance):
   - Get earliest timestamp
   - Calculate relative time ("Just now", "X minutes ago", etc.)
   - Build source_label from channel_tag if available
   - For each message in group (while budget > 0):
     - Append message with role label
6. Reverse sections back to chronological order
7. Return joined prompt

**Returns:** `str` (short-term memory section)

**Config Parameters Used:**
- `SHORT_TERM_TOKEN_LIMIT = 40000` (character limit)
- `SHORT_TERM_MEMORY_HEADER` (header text)

---

#### **42. ContextRuntime._build_auxiliary_narratives_prompt()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/context_runtime/context_runtime.py`
**Lines:** 462-499

**Signature:**
```python
async def _build_auxiliary_narratives_prompt(
    self,
    auxiliary_summaries: List[Dict[str, Any]],
    evermemos_memories: Optional[Dict[str, Any]] = None
) -> str
```

**Steps:**
1. Start with AUXILIARY_NARRATIVES_HEADER
2. For each auxiliary summary:
   - Add narrative name, summary (topic_hint), event count
   - **Phase 3 enhancement:** If evermemos_memories contains this narrative_id:
     - Extract episode_summaries (up to 3)
     - Add "Related Content" section
     - Truncate long summaries (max 150 chars)

**Returns:** `str` (auxiliary narratives section)

---

### **SESSION MANAGEMENT**

#### **43. SessionService.get_or_create_session()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/narrative/session_service.py`
**Lines:** 210-288

**Signature:**
```python
async def get_or_create_session(
    self,
    user_id: str,
    agent_id: str
) -> ConversationSession
```

**Steps:**
1. Acquire asyncio.Lock
2. Create key: `(user_id, agent_id)`
3. **Step 1: Check memory cache**
   - If key in _sessions, get session
4. **Step 2: Check file (if not in memory)**
   - Call `_load_session_from_file(agent_id, user_id)`
   - If loaded, store in memory cache and session_by_id index
5. **Step 3: Check timeout**
   - If session exists:
     - Calculate `elapsed = (datetime.now(timezone.utc) - session.last_query_time).total_seconds()`
     - If `elapsed <= config.SESSION_TIMEOUT` (default 600s):
       - Return existing session
     - Else:
       - Delete old session via `_remove_session_with_file()`
6. **Step 4: Create new session** (if no valid existing session)
   - Call `_create_new_session(user_id, agent_id)`
   - Store in memory and file
   - Generate session_id: `f"sess_{uuid4().hex[:16]}"`

**Returns:** `ConversationSession`

**External Calls:**
- `_load_session_from_file()` → file I/O with fcntl lock
- `_save_session_to_file()` → persist to disk
- `_remove_session_with_file()` → delete memory + file

**Config Parameters Used:**
- `config.SESSION_TIMEOUT` (default 600 seconds = 10 minutes)

---

#### **44. SessionService.save_session()**
**File:** `/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/src/xyz_agent_context/narrative/session_service.py`
**Lines:** 184-208

**Signature:**
```python
async def save_session(self, session: ConversationSession) -> None
```

**Steps:**
1. Acquire asyncio.Lock
2. Update memory index: `_session_by_id[session.session_id] = session`
3. Update primary cache: `_sessions[(session.user_id, session.agent_id)] = session`
4. Persist to file: `_save_session_to_file(session)`

**Returns:** None

**External Calls:**
- `_save_session_to_file()` → file I/O with fcntl lock

---

---

## SUMMARY OF CONFIGURATION PARAMETERS

**Key config values used across pipeline:**
- `config.MAX_NARRATIVES_IN_CONTEXT` (default retrieval count, e.g., 5)
- `config.NARRATIVE_SEARCH_TOP_K` (vector search multiplier)
- `config.NARRATIVE_MATCH_HIGH_THRESHOLD` (high confidence score, e.g., 0.8)
- `config.NARRATIVE_MATCH_LOW_THRESHOLD` (low confidence score, e.g., 0.3)
- `config.NARRATIVE_MATCH_THRESHOLD` (general threshold, e.g., 0.5)
- `config.NARRATIVE_MATCH_USE_LLM` (enable LLM judgment)
- `config.VECTOR_SEARCH_MIN_SCORE` (min similarity score for vector search)
- `config.EVERMEMOS_ENABLED` (enable EverMemOS HTTP API mode)
- `config.EMBEDDING_UPDATE_INTERVAL` (events before embedding refresh, e.g., 5)
- `config.NARRATIVE_LLM_UPDATE_INTERVAL` (events before LLM update, e.g., 5)
- `config.NARRATIVE_LLM_UPDATE_EVENTS_COUNT` (recent events for LLM context)
- `config.SUMMARY_MAX_LENGTH` (topic_hint max length, e.g., 500)
- `config.RECENT_EVENTS_WEIGHT` (blending weight for event enhancement, e.g., 0.3)
- `config.MATCH_RECENT_EVENTS_COUNT` (max recent events to consider for scoring)
- `config.EVERMEMOS_EPISODE_SUMMARIES_PER_NARRATIVE` (max summaries extracted)
- `config.EVERMEMOS_EPISODE_CONTENTS_PER_NARRATIVE` (max raw contents extracted)
- `config.SESSION_TIMEOUT` (session timeout seconds, default 600)
- `config.CONTINUITY_LLM_MODEL` (Claude model for continuity detection)
- `config.NARRATIVE_JUDGE_LLM_MODEL` (Claude model for LLM judgment)

---

This exhaustive trace covers every function with complete signature, step-by-step operations, external calls, return types, and configuration parameters used throughout the entire narrative selection, retrieval, update, and context building pipeline.