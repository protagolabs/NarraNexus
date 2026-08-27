"""
@file_name: landings.py
@author: NetMind.AI
@date: 2026-08-27
@description: The executors every routing decider shares — candidate
              labelling for prompts, and the landings for match / participant
              verdicts — plus the Landing value object.

Extracted from retrieval.py on review (2026-08-27 round 3, I3): the four
methods were a cohesive unit ("change the decider, keep the executors") lost
inside a 1,300-line module, and `Landing` lived in merged_select — which
forced the flag-off path to import the whole merged module (and its
helper-SDK chain) for a six-field dataclass (M4).

`_candidate_labels` moved WITH its four consumers — it is the ONE definition
of what a candidate shows a model, and it must not fork (the judge's two
branches were two copies of that decision once, and only one was fixed).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple, TYPE_CHECKING

from ..models import Narrative, NarrativeSearchResult

if TYPE_CHECKING:
    from .crud import NarrativeCRUD


CANDIDATE_DESC_MAX_CHARS = 300  # Summary excerpt shown per candidate


@dataclass(frozen=True)
class Landing:
    """Where one turn landed — every field a verdict must answer, at once.

    Constructed whole by every verdict branch (merged path) and by
    `_land_no_topic_turn` (both paths): the alternative — six loose locals
    assigned per-branch — is how a new result field gets set on four verdicts
    and silently defaulted on the fifth.
    """

    narratives: List[Narrative]
    method: str
    reason: str
    retrieval_method: str
    is_new: bool = False
    no_durable_topic: bool = False


def _candidate_labels(narrative: Narrative) -> Tuple[str, str]:
    """The (name, description) a narrative shows the LLM judge — ONE definition.

    Every branch that assembles a judge candidate goes through here. That is
    the actual fix, not an aesthetic one: the search branch and the PARTICIPANT
    branch of `_llm_unified_match` were two implementations of this same
    decision, 50 lines apart, and on 2026-04-15 only the search branch was
    moved onto the live `narrative_info` fields. The PARTICIPANT branch kept
    reading `topic_hint`, which the 2026-06-09 unified-memory refactor then
    froze into a write-once-at-creation tombstone — 84% empty on the local dev
    DB, and stale wherever it is not. Measured worst cases: a 72-event
    narrative described to the judge by its first sentence from three months
    earlier, and one whose label was a `[:50]` cut through the middle of an
    open_id. That branch FORCES the judge to run (a task someone invited the
    user into must not lose to a keyword hit on the user's own narrative), so
    a blind label there decides the turn.

    "Untitled" with an empty description is the honest answer for a narrative
    whose metadata the async updater has not written yet; a frozen creation-time
    hint is not, because it reads to the LLM as current fact.
    """
    info = narrative.narrative_info
    name = (info.name if info and info.name else "") or "Untitled"
    summary = (info.current_summary if info and info.current_summary else "")
    return name, summary[:CANDIDATE_DESC_MAX_CHARS]

async def build_menu_candidates(
    crud: "NarrativeCRUD", results: Sequence[NarrativeSearchResult]
) -> List[dict]:
    """Load and label the menu rows a routing prompt will show.

    Goes through `_candidate_labels` — the ONE definition of what a
    candidate shows a model. The judge's two branches were two copies of
    that decision once, and only one of them was ever fixed; a third copy
    here is how that repeats.
    """
    candidates: List[dict] = []
    for result in results:
        narrative = await crud.load_by_id(result.narrative_id)
        if narrative is None:
            continue
        name, description = _candidate_labels(narrative)
        candidates.append({
            "id": narrative.id,
            "type": "search",
            "name": name,
            "description": description,
            "score": result.similarity_score,
            "raw_score": result.raw_score,
            "matched_terms": result.matched_terms,
            "matched_content": result.matched_snippet,
        })
    return candidates

def build_participant_candidates(
    narratives: Sequence[Narrative],
) -> List[dict]:
    """Label PARTICIPANT threads for a prompt. Same labeller, and
    deliberately no evidence fields: these never went through BM25 (they
    enter at a synthetic neutral score), and inventing evidence for them
    would be worse than showing none."""
    candidates: List[dict] = []
    for narrative in narratives:
        name, description = _candidate_labels(narrative)
        candidates.append({
            "id": narrative.id,
            "type": "participant",
            "name": name,
            "description": description,
        })
    return candidates

async def assemble_match_landing(
    crud: "NarrativeCRUD",
    matched_id: str,
    search_results: Sequence[NarrativeSearchResult],
    top_k: int,
) -> List[Narrative]:
    """The chosen thread first, then the rest of the ranked set.

    Extracted from `_llm_unified_match`'s search branch so the merged router
    lands a `match` verdict through the SAME executor. The whole shape of
    this batch is "change the decider, keep the executors" — a second copy
    of this loop would be the first crack in that.
    """
    narratives: List[Narrative] = []
    matched = await crud.load_by_id(matched_id)
    if matched:
        narratives.append(matched)
    for result in search_results[:top_k]:
        if result.narrative_id == matched_id:
            continue
        narrative = await crud.load_by_id(result.narrative_id)
        if narrative and len(narratives) < top_k:
            narratives.append(narrative)
    return narratives

async def load_participant_landing(
    crud: "NarrativeCRUD", matched_id: Optional[str]
) -> List[Narrative]:
    """The participant verdict's landing — one loader, both deciders.

    Same reasoning as `assemble_match_landing`: the judge and the merged
    router must land a participant verdict through the SAME executor, or
    the first added line (trailing context, surface guard) forks them.
    """
    matched = await crud.load_by_id(matched_id) if matched_id else None
    return [matched] if matched else []
