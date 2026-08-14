"""
@file_name: narrative_config.py
@author: NetMind.AI
@date: 2025-11-24
@description: Global configuration for the narrative retrieval system

All tunable parameters are centralized in this file for easy experimentation and tuning
"""
import os


def _env(name: str, default: str) -> str:
    """Read an env override for a config knob, falling back to `default`.

    Used for the narrative model / reasoning_effort knobs so two backends
    can be started side-by-side with different settings for A/B comparison
    without editing this file each time.
    """
    value = os.environ.get(name)
    return value if value else default


class NarrativeConfig:
    """Global configuration for the narrative retrieval system"""

    # ==================== Session Management ====================

    # NOTE (2026-05-20 short-term-memory continuity fix): the session timeout
    # was REMOVED. The session is the continuity anchor for the user's chat
    # box — the user can reply to a visible message minutes, hours, or days
    # later (e.g. answering a question the agent sent from a scheduled job),
    # so the anchor must persist indefinitely. Sessions are one tiny,
    # overwritten-in-place file per (agent, user); they do not grow without
    # bound, so there is nothing to time out. (Was SESSION_TIMEOUT = 600.)
    # The fast path (step_1_fast_select) honors the same rule: a live
    # anchor is reused regardless of age. It briefly reintroduced a
    # 30-minute window (2026-08-14) — removed the same day for exactly
    # the reason above.

    # ==================== Continuity Detection (LLM version) ====================

    # Continuity detection model
    # Description: LLM model used to determine topic continuity
    # Default: gpt-5.4-mini-2026-03-17 (reasoning model, fast w/ low effort).
    # Env override: NARRATIVE_CONTINUITY_MODEL
    CONTINUITY_LLM_MODEL = _env("NARRATIVE_CONTINUITY_MODEL", "gpt-5.4-mini-2026-03-17")

    # Continuity detection reasoning effort
    # Description: reasoning_effort passed to GPT-5.4 reasoning models.
    # OpenAI's chat.completions API accepts: "none" / "low" / "medium"
    # / "high" / "xhigh". Note: the openai-agents pydantic Literal still
    # lists "minimal" instead of "none" (lib lag, 2026-05); don't trust
    # it — sending "minimal" gives 400 invalid_request_error from
    # OpenAI server-side, observed in prod 2026-05-12. "low" is the
    # smallest budget that both layers accept, and benches sub-second.
    # Env override: NARRATIVE_CONTINUITY_EFFORT
    CONTINUITY_LLM_REASONING_EFFORT = _env("NARRATIVE_CONTINUITY_EFFORT", "low")

    # Narrative judge model
    # Description: LLM model used for narrative matching/judge decisions.
    # Default: gpt-5.4-mini-2026-03-17. Env override: NARRATIVE_JUDGE_MODEL
    NARRATIVE_JUDGE_LLM_MODEL = _env("NARRATIVE_JUDGE_MODEL", "gpt-5.4-mini-2026-03-17")

    # Narrative judge reasoning effort
    # Description: reasoning_effort passed to GPT-5.4 reasoning models.
    # See CONTINUITY_LLM_REASONING_EFFORT for why "low" (not "minimal").
    # Env override: NARRATIVE_JUDGE_EFFORT
    NARRATIVE_JUDGE_LLM_REASONING_EFFORT = _env("NARRATIVE_JUDGE_EFFORT", "low")

    # LLM call maximum retry count
    # Description: Number of retries when LLM API call fails
    # Recommended: 3
    CONTINUITY_LLM_MAX_RETRIES = 3

    # ==================== Narrative Matching ====================

    # ==================== Narrative Matching Thresholds (Two-tier threshold + Unified LLM judgment) ====================

    # High-confidence gate — see _narrative_impl/routing_gate.py for the full
    # rationale. Both conditions must hold before BM25 is trusted to route a
    # turn without LLM arbitration.
    #
    # Replaces NARRATIVE_MATCH_HIGH_THRESHOLD = 0.70, which compared the
    # SQUASHED score s/(s+1) and so meant "raw >= 2.33" — cleared by incidental
    # CJK character collisions. Measured on 113 real prod turns across 22
    # agents, that rule short-circuited 86.7% of routing decisions, i.e. the
    # LLM arbitration tier was effectively dead code.
    #
    # RAW_FLOOR is a NOISE filter, deliberately low — NOT a strength test.
    # Raw BM25 scales with query length (measured median top1: 5.3 for queries
    # under 40 chars vs 12-15 for over 40), so a high absolute floor
    # systematically rejects short follow-up turns regardless of whether the
    # match is right — and short follow-ups are exactly where misrouting hurts.
    # The MARGIN does the discrimination: within one query all candidates share
    # an IDF table, so the spread is meaningful even though the absolute value
    # is not.
    #
    # MARGIN_RATIO is set on an asymmetric cost: a false defer costs one helper
    # LLM call and the LLM usually confirms; a false accept poisons the thread
    # and gets locked in for several turns by the `continuous` path, which
    # re-uses session.current_narrative_id with no topic check. Prefer
    # deferring. At 2.0 the eval set short-circuits 54.9% of turns.
    NARRATIVE_MATCH_RAW_FLOOR = 3.0
    NARRATIVE_MATCH_MARGIN_RATIO = 2.0

    # Fast-path (TurnProfile bm25_top1) anchor-override floor.
    # The fast path has no continuity LLM: when the session carries a live
    # anchor (current_narrative_id), that thread is reused by default and a
    # BM25 top-1 may steal the turn away from it ONLY above this raw score.
    # Deliberately high — raw BM25 scales with query length (median top1:
    # ~5.3 under 40 chars vs 12-15 over, see RAW_FLOOR note above), so short
    # follow-ups can never clear it and stay in their thread, while a long,
    # topic-rich message that decisively matches another thread still
    # switches. Distinct from RAW_FLOOR, which is a noise filter for the
    # anchorless case. Env override: NARRATIVE_FAST_ANCHOR_OVERRIDE_FLOOR
    FAST_ANCHOR_OVERRIDE_FLOOR = float(
        _env("NARRATIVE_FAST_ANCHOR_OVERRIDE_FLOOR", "12.0")
    )

    # Fast-path new-thread gate: with a live anchor, a turn may open a NEW
    # narrative only when BM25 found nothing above the noise floor (top-1
    # sub-floor implies the anchor is sub-floor too — scores are ranked)
    # AND the query is big enough for that silence to be trusted: an
    # elliptical follow-up ("ok", "继续", "好的谢谢") scores zero against
    # everything yet must stay in its thread, while a full sentence with
    # zero overlap against every narrative is a genuinely new topic.
    #
    # Measured in script-independent UNITS (narrative_service.query_units:
    # 1 per CJK character — han/kana/hangul carry roughly a word each —
    # plus 1 per whitespace token of the rest). A plain char count would
    # be blind to CJK: a complete 12-char Chinese sentence is a full new
    # topic, not a "short" message, and a char threshold tuned on English
    # prose would keep the gate shut for zh users essentially forever.
    #
    # Known residual bias, on purpose and in EVERY language: a new-topic
    # message under the unit threshold (e.g. a 6-word English command)
    # stays in the old thread until a fuller message opens the new one —
    # one mis-filed turn beats a thread-per-"ok" (the fragmentation
    # failure this gate replaces). The default is a provisional
    # calibration; retune from gate_top1_raw once fast-path audit rows
    # accumulate. NOT a time window (see the 2026-05-20 NOTE).
    # Env override: NARRATIVE_FAST_NEW_THREAD_MIN_QUERY_UNITS
    FAST_NEW_THREAD_MIN_QUERY_UNITS = int(
        _env("NARRATIVE_FAST_NEW_THREAD_MIN_QUERY_UNITS", "8")
    )

    # Below high threshold: < this value, unified LLM judgment (considering both search results and default Narratives)
    # LLM will determine:
    #   - Whether it matches searched Narratives (returns a list)
    #   - Whether it matches default Narratives (returns only 1)
    #   - Or create a new Narrative

    # Whether to enable LLM-assisted matching (when score is below high threshold)
    # Description: When True, scenarios below high threshold are handled by unified LLM judgment
    #      When False, scores below high threshold directly create a new Narrative (not recommended)
    NARRATIVE_MATCH_USE_LLM = True

    # Narrative retrieval Top-K
    # Description: Returns the top K most similar Narrative candidates during retrieval
    # Recommended: 3
    # Purpose: Can put Top-3 into Context for Agent reference (optional)
    NARRATIVE_SEARCH_TOP_K = 3

    # Number of Narratives added to Context
    # Description: Upper limit of Narratives returned by select()
    # Recommended: 3 (1 main Narrative + 2 auxiliary references)
    MAX_NARRATIVES_IN_CONTEXT = 3

    # Medium continuity weighting factor
    # Description: When continuity detection judges as "medium", weight the current Session's Narrative
    # Range: 1.0-1.5
    # Recommended: 1.2 (20% boost)
    # Purpose: Bias toward continuing the current topic when uncertain
    CONTINUITY_BOOST_FACTOR = 1.2

    # ==================== Event Selection Strategy ====================

    # Most recent N Events
    # Description: Always load the Narrative's most recent N Events to ensure continuity
    # Recommended: 3
    MAX_RECENT_EVENTS = 3

    # Maximum Event count in Context
    # Description: Upper limit of total Events added to Context (after deduplication)
    # Recommended: 6
    MAX_EVENTS_IN_CONTEXT = 6

    # ==================== Narrative LLM Dynamic Update ====================
    # Use LLM to update Narrative metadata every N Events (name, current_summary,
    # actors, topic_keywords, dynamic_summary). Default 1 = every Event; raise to
    # 3-5 to reduce LLM call costs. (Moved back from the package-root config.py
    # 2026-07-24 — narrative/ was its only consumer, the root file is gone.)
    NARRATIVE_LLM_UPDATE_INTERVAL = 1

    # LLM model used for updates
    # Description: LLM model used for generating Narrative summaries and metadata.
    # Default: gpt-5.4-mini-2026-03-17. Env override: NARRATIVE_UPDATE_MODEL
    NARRATIVE_LLM_UPDATE_MODEL = _env("NARRATIVE_UPDATE_MODEL", "gpt-5.4-mini-2026-03-17")

    # Narrative update reasoning effort.
    # Description: summary updates run post-turn in the background, so they
    # are not on the critical path — "low" is fine. See
    # CONTINUITY_LLM_REASONING_EFFORT for why "low" (not "minimal").
    # Env override: NARRATIVE_UPDATE_EFFORT
    NARRATIVE_LLM_UPDATE_REASONING_EFFORT = _env("NARRATIVE_UPDATE_EFFORT", "low")

    # Number of recent Events considered during LLM update
    # Description: Generates summaries based on the most recent N Events
    # Recommended: 5
    NARRATIVE_LLM_UPDATE_EVENTS_COUNT = 5


    # Summary maximum length
    # Description: Maximum character count for topic_hint
    # Recommended: 200
    SUMMARY_MAX_LENGTH = 200

    # ==================== Hierarchical Structure (Reserved for Phase 2) ====================

    # Whether to enable hierarchical tree structure
    # Description: Set to False in Phase 1, all Narratives are flat
    # Set to True in Phase 2 to enable Root/Children structure
    ENABLE_HIERARCHICAL_STRUCTURE = False

    # Beam Search width (used in tree-based retrieval)
    # Description: Number of candidates retained per level during tree search
    # Recommended: 3
    # Only effective when ENABLE_HIERARCHICAL_STRUCTURE=True
    BEAM_SEARCH_WIDTH = 3

    # Root level similarity threshold
    # Description: Minimum matching threshold between Query and Root Narrative
    # Recommended: 0.70
    # Only effective when ENABLE_HIERARCHICAL_STRUCTURE=True
    ROOT_MATCH_THRESHOLD = 0.70

    # Child level similarity threshold
    # Description: Minimum matching threshold between Query and Child Narrative
    # Recommended: 0.75
    # Only effective when ENABLE_HIERARCHICAL_STRUCTURE=True
    CHILD_MATCH_THRESHOLD = 0.75

    # ==================== Narrative Splitting (Reserved for Phase 2) ====================

    # Whether to enable automatic splitting
    # Description: Set to False in Phase 1, no automatic splitting
    ENABLE_AUTO_SPLIT = False

    # Maximum Event count (split trigger condition 1)
    # Description: When a Narrative's Event count exceeds this value, trigger split detection
    # Recommended: 20
    MAX_EVENTS_PER_NARRATIVE = 20

    # Topic coherence threshold (split trigger condition 2)
    # Description: When average similarity of last N Events to Narrative topic falls below this, trigger split
    # Recommended: 0.60
    TOPIC_COHERENCE_THRESHOLD = 0.60

    # Coherence check window (Event count)
    # Description: Number of recent Events considered when checking topic coherence
    # Recommended: 3
    COHERENCE_CHECK_WINDOW = 3

    # ==================== Debugging and Logging ====================

    # Whether to enable verbose logging
    # Description: Outputs detailed information about continuity detection, similarity computation, etc.
    # Development phase: True
    # Production environment: False
    ENABLE_VERBOSE_LOGGING = True

    # Whether to log similarity scores
    # Description: Records all similarity computation results in Narrative metadata
    # Purpose: For subsequent analysis and parameter tuning
    LOG_SIMILARITY_SCORES = True


# Export config instance (singleton)
config = NarrativeConfig()
