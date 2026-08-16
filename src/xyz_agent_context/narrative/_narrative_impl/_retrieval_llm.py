"""
@file_name: _retrieval_llm.py
@author: Bin Liang
@date: 2026-03-06
@description: LLM-based Narrative match judgment logic

Extracted from retrieval.py. Contains:
- LLM output schema definitions
- Unified multi-candidate judgment (llm_judge_unified)

These are pure LLM judgment functions with no dependency on NarrativeRetrieval state.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel
from loguru import logger

from xyz_agent_context.agent_framework.llm.helper_sdk import get_helper_sdk
from ..config import config
from .prompts import (
    NARRATIVE_UNIFIED_MATCH_WITH_PARTICIPANT_INSTRUCTIONS,
    NARRATIVE_UNIFIED_MATCH_INSTRUCTIONS,
)


# ===== LLM output schema definitions =====

class UnifiedMatchOutput(BaseModel):
    """
    LLM unified match output structure

    Used for the output of the llm_judge_unified function.
    """
    reason: str  # Detailed reasoning process
    matched_category: str  # "search", "no_durable_topic", or "none"
    matched_index: int  # Matched index (0-based), -1 unless matched_category="search"


# ===== LLM judgment functions =====

async def llm_judge_unified(
    query: str,
    search_candidates: List[dict],
    default_candidates: List[dict],
    participant_candidates: Optional[List[dict]] = None,
) -> dict:
    """
    LLM unified judgment: Considers search results, default Narratives, and PARTICIPANT Narratives

    Args:
        query: User query
        search_candidates: Search result candidates [{"id", "type": "search", "name", "description", "score"}]
        default_candidates: Default Narrative candidates [{"id", "type": "default", "name", "description", "examples"}]
        participant_candidates: PARTICIPANT Narratives [{"id", "type": "participant", "name", "description"}]

    Returns:
        {
            "matched_id": str/None,
            "matched_type": "default"/"search"/"participant"/None,
            "reason": str
        }
    """
    if not search_candidates and not default_candidates and not participant_candidates:
        return {"matched_id": None, "matched_type": None, "reason": "No candidates"}

    has_participant_context = participant_candidates and len(participant_candidates) > 0

    try:
        # Adjust instructions based on whether PARTICIPANT candidates exist
        if has_participant_context:
            instructions = NARRATIVE_UNIFIED_MATCH_WITH_PARTICIPANT_INSTRUCTIONS
        else:
            instructions = NARRATIVE_UNIFIED_MATCH_INSTRUCTIONS

        # Build candidate list
        user_input = ""

        # 0. PARTICIPANT Narratives - placed first to emphasize importance
        if participant_candidates:
            user_input += "## Participant-Associated Topics (user is a PARTICIPANT):\n\n"
            for i, candidate in enumerate(participant_candidates):
                user_input += f"[Participant-{i}] {candidate['name']}\n"
                user_input += f"Description: {candidate['description']}\n"
                user_input += "\n"

        # 1. Default Narratives — empty under bucket governance (C-1). The
        # eight category names still reach the model, as VOCABULARY inside the
        # instructions, so it can still recognise "no durable topic"; what it
        # can no longer do is file the turn INTO one of them.
        if default_candidates:
            user_input += "## Default Topic Types:\n\n"
            for i, candidate in enumerate(default_candidates):
                user_input += f"[Default-{i}] {candidate['name']}\n"
                user_input += f"Description: {candidate['description']}\n"
                if candidate.get('examples'):
                    user_input += f"Examples: {', '.join(candidate['examples'][:3])}\n"
                user_input += "\n"

        # 2. Search results (BM25 keyword candidates)
        if search_candidates:
            user_input += "## Existing Topics:\n\n"
            for i, candidate in enumerate(search_candidates):
                user_input += f"[Topic-{i}] {candidate['name']}\n"
                user_input += f"Description: {candidate['description']}\n"
                user_input += f"Similarity score: {candidate['score']:.2f}\n"
                # WHY it scored that. The score is a per-candidate-set BM25
                # value squashed into (0,1), so it carries no absolute meaning
                # and hides the difference between "matched the topic" and
                # "matched 帮/查/一/下". These two lines are what let the judge
                # answer "none of these" on a crowded set of frame-word
                # collisions instead of picking the least-bad row.
                if candidate.get('matched_terms'):
                    user_input += f"Matched terms: {', '.join(candidate['matched_terms'])}\n"
                if candidate.get('matched_content'):
                    user_input += f"Matched content:\n{candidate['matched_content']}\n"
                elif candidate.get('raw_score', 0.0) > 0:
                    # A candidate with a real BM25 score hit at least one query
                    # term, so its evidence cannot legitimately be empty. This
                    # branch spent 2026-04-15 → 2026-08-12 as the ONLY branch
                    # (the writer was deleted while the reader lived in this
                    # file, and its `logger.debug` sibling fired every turn for
                    # four months without anyone reading it); if it fires now,
                    # the wiring broke again. Candidates at raw_score 0.0 are
                    # the participant narratives merged in at a synthetic score
                    # — they never went through BM25 and owe nothing, so they
                    # must not trip this alarm (incident lesson #3: an alarm
                    # that cries wolf gets silenced, and then it is gone).
                    logger.warning(
                        f"[NarrativeJudge] search candidate {i} "
                        f"({candidate.get('id')}) scored "
                        f"{candidate.get('raw_score')} but carries no BM25 "
                        f"evidence — rank_pool → _llm_unified_match wiring is broken"
                    )
                user_input += "\n"

        user_input += f"## User's New Query:\n{query}\n\n"
        user_input += "Please determine which candidate the user query should match, or create a new topic."

        sdk = get_helper_sdk()
        result = await sdk.llm_function(
            instructions=instructions,
            user_input=user_input,
            output_type=UnifiedMatchOutput,
            model=config.NARRATIVE_JUDGE_LLM_MODEL,
            reasoning_effort=config.NARRATIVE_JUDGE_LLM_REASONING_EFFORT or None,
        )
        output: UnifiedMatchOutput = result.final_output

        # Parse result — prioritize PARTICIPANT match
        if output.matched_category == "participant":
            if participant_candidates and 0 <= output.matched_index < len(participant_candidates):
                matched_id = participant_candidates[output.matched_index]["id"]
                logger.info(f"LLM matched PARTICIPANT Narrative (index={output.matched_index}): {matched_id}")
                return {
                    "matched_id": matched_id,
                    "matched_type": "participant",
                    "reason": output.reason
                }
            else:
                logger.warning(f"LLM returned participant index={output.matched_index} out of range")

        elif output.matched_category == "no_durable_topic":
            # A verdict about the TURN, carrying no destination. The caller
            # (retrieval -> NarrativeService.select) decides where it lands;
            # see the anchor-first rule in select().
            logger.info(f"LLM: no durable topic this turn — {output.reason[:120]}")
            return {
                "matched_id": None,
                "matched_type": "no_topic",
                "reason": output.reason,
            }

        elif output.matched_category == "default":
            if 0 <= output.matched_index < len(default_candidates):
                matched_id = default_candidates[output.matched_index]["id"]
                logger.info(f"LLM matched default Narrative (index={output.matched_index}): {matched_id}")
                return {
                    "matched_id": matched_id,
                    "matched_type": "default",
                    "reason": output.reason
                }
            else:
                logger.warning(f"LLM returned default index={output.matched_index} out of range")

        elif output.matched_category == "search":
            if 0 <= output.matched_index < len(search_candidates):
                matched_id = search_candidates[output.matched_index]["id"]
                logger.info(f"LLM matched search result (index={output.matched_index}): {matched_id}")
                return {
                    "matched_id": matched_id,
                    "matched_type": "search",
                    "reason": output.reason
                }
            else:
                logger.warning(f"LLM returned search index={output.matched_index} out of range")

        # matched_category == "none" or error
        logger.info(f"LLM determined no match with any Narrative: {output.reason}")
        return {
            "matched_id": None,
            "matched_type": None,
            "reason": output.reason
        }

    except Exception as e:
        logger.warning(f"LLM unified judgment failed: {e}")
        return {
            "matched_id": None,
            "matched_type": None,
            "reason": f"LLM call failed: {str(e)}"
        }
