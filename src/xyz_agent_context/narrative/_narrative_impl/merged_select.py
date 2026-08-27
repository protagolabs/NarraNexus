"""
@file_name: merged_select.py
@author: NetMind.AI
@date: 2026-08-27
@description: Orchestration of the merged routing path — one decision per
              turn: BM25 first, then a shutter or ONE LLM call.

Moved out of NarrativeService on review (2026-08-27, Important #2): the
service file had grown past the 800-line bound with two 250-line deciders
side by side, and every verdict branch hand-assigned six loose locals. Here
each landing is a small function returning a frozen ``Landing`` — six fields
constructed at once, so a new ``NarrativeSelectionResult`` field cannot be
silently defaulted on one verdict and set on another.

The service keeps a thin ``_select_merged`` delegate; ``is_reusable_anchor``
and ``minutes_since`` stay ONE definition each (anchor_rules.py), shared with
``select()``, ``_land_no_topic_turn`` and ``step_1_fast_select``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, TYPE_CHECKING

from loguru import logger

from ..config import config
from ..models import (
    ConversationSession,
    Narrative,
    NarrativeSelectionResult,
    NarrativeType,
    RoutingAudit,
)
from .anchor_rules import is_reusable_anchor, minutes_since
from .merged_router import (
    MergedRoutingDecision,
    MergedRoutingInput,
    VERDICT_CONTINUE_ANCHOR,
    VERDICT_MATCH,
    VERDICT_NEW,
    VERDICT_NO_TOPIC,
    VERDICT_PARTICIPANT,
    decide,
    pick_menu,
    resolve_choice,
)

if TYPE_CHECKING:
    from ..narrative_service import NarrativeService


@dataclass(frozen=True)
class Landing:
    """Where one turn landed — every field a verdict must answer, at once.

    The five merged verdict branches (and the no-topic landing in the
    service) each construct this whole; the alternative — six loose locals
    assigned per-branch — is how a new result field gets set on four verdicts
    and silently defaulted on the fifth.
    """

    narratives: List[Narrative]
    method: str
    reason: str
    retrieval_method: str
    is_new: bool = False
    no_durable_topic: bool = False


async def select_merged(
    service: "NarrativeService",
    *,
    agent_id: str,
    user_id: str,
    query_text: str,
    max_narratives: int,
    session: Optional[ConversationSession],
    awareness: Optional[str],
    is_user_chat: bool,
    trigger: str,
) -> NarrativeSelectionResult:
    """One decision per turn: BM25 first, then a shutter or ONE call.

    WHY (specs/2026-08-25-merged-routing-design.md §4)

    The two-call path asks continuity ("does this continue?") and then, if
    that says no, the judge ("so where does it go?"). Prod, 7 days,
    is_user_chat=1, n=189: 43 turns paid for both, serial p50 8,924ms / mean
    13,004ms, while the entire non-LLM half of routing is 47.6ms mean. The
    only lever with anything behind it is the number of round trips.

    THE DECIDER CHANGES, THE EXECUTORS DO NOT

    Every landing below is a pre-existing code path: the continuity landing
    (return the anchor), `assemble_match_landing` (the judge's own search
    landing), `load_participant_landing`, `create_from_query`,
    `_land_no_topic_turn`. Downstream — step_1, step_4, the ChatModule —
    reads `narratives` / `is_new` / `no_durable_topic` / `retrieval_method`
    and never branches on `selection_method`, so nothing outside this path
    learns there was a change.

    WHAT THIS OPENS, AND WHAT PAYS FOR IT (§3.2)

    On the two-call path a continuity turn returned before the retrieval
    tier: no pool, no menu, no way for a foreign thread to reach a prompt.
    That was an unnamed defence, and it is the one that held the p07 hijack
    specimen for eleven turns. Merging removes it, so the anchored thread is
    injected into the prompt unconditionally and deduplicated out of the
    menu, and `anchor_bm25_rank` / `anchor_in_menu` are recorded on every row
    so the compensation can actually be measured in production instead of
    assumed.
    """
    from xyz_agent_context.utils.logging import timed

    audit = RoutingAudit(
        agent_id=agent_id, user_id=user_id, query_text=query_text,
        trigger=trigger, is_user_chat=is_user_chat,
    )
    # The PATH, not the LLM: a turn the shutter releases took this path and
    # asked nobody.
    audit.merged_call = True
    snapshots: dict = {}

    anchor: Optional[Narrative] = None
    anchor_id = session.current_narrative_id if session else None
    if anchor_id:
        anchor = await service._crud.load_by_id(anchor_id)
    # THE one definition, shared with the fast path and the no-topic
    # landing: a legacy default bucket is a verdict about an earlier turn,
    # not a thread anyone may continue.
    continuable = is_reusable_anchor(anchor)

    with timed("narrative.merged.prepare"):
        prep = await service._retrieval.prepare_merged_routing(
            query=query_text,
            user_id=user_id,
            agent_id=agent_id,
            # Passed only when it is a thread the turn could legitimately
            # stay on, so the shutter cannot open onto a container.
            anchor_narrative_id=anchor.id if (anchor and continuable) else None,
            is_user_chat=is_user_chat,
            audit=audit,
            snapshots=snapshots,
            menu_size=config.MERGED_MENU_SIZE,
        )

    # `anchor_match` is the only verdict that opens the shutter, and it is
    # unreachable without the anchor id passed above — so the `anchor is not
    # None` half is belt-and-braces, and it is written as a CONDITION rather
    # than a comment because the alternative (asserting it in prose and
    # indexing anyway) is how a "cannot happen" becomes a None in a list.
    if prep.shutter_granted and anchor is not None:
        landing = Landing(
            narratives=[anchor],
            method="anchor_confirmed",
            reason=f"Confirmed the anchored thread: {prep.bypass.detail}",
            retrieval_method="session",
        )
        logger.info(f"[NarrativeSelect] shutter — {prep.bypass.detail}")
    else:
        excluded: set = set(n.id for n in prep.participant_narratives)
        if anchor is not None:
            excluded.add(anchor.id)
        menu_results = pick_menu(
            prep.ranked, exclude_ids=excluded, limit=config.MERGED_MENU_SIZE
        )
        routing_input = MergedRoutingInput(
            query=query_text,
            anchor=anchor,
            anchor_is_continuable=bool(anchor is not None and continuable),
            previous_query=(session.last_query or "") if session else "",
            previous_response=(session.last_response or "") if session else "",
            minutes_since_previous=minutes_since(session),
            menu=await service._retrieval.build_menu_candidates(menu_results),
            participants=service._retrieval.build_participant_candidates(
                prep.participant_narratives
            ),
            awareness=awareness,
        )

        with timed("narrative.merged.decide") as t:
            decision = await decide(routing_input)
            # Tag the timer with the model the helper LLM actually used
            # (resolved deep in the SDK; read back via its contextvar).
            from xyz_agent_context.agent_framework.adapters.openai_agents import (
                get_last_llm_call_info,
            )
            info = get_last_llm_call_info()
            if info:
                t.tag(**info)

        audit.merged_verdict = decision.verdict
        audit.merged_ms = decision.elapsed_ms
        if decision.prompt is not None:
            audit.merged_input_chars = decision.prompt.input_chars
            audit.merged_truncated = ",".join(decision.prompt.truncated)

        landing = await _land(
            service,
            decision=decision,
            routing_input=routing_input,
            menu_results=menu_results,
            anchor=anchor,
            continuable=continuable,
            session=session,
            agent_id=agent_id,
            user_id=user_id,
            query_text=query_text,
            max_narratives=max_narratives,
        )

    service._advance_session_anchor(
        session, query_text, landing.narratives, is_user_chat
    )

    logger.info(
        f"[NarrativeSelect] merged: {len(landing.narratives)} Narratives, "
        f"method={landing.method}"
    )

    audit.selection_method = landing.method
    audit.retrieval_method = landing.retrieval_method
    audit.chosen_narrative_id = (
        landing.narratives[0].id if landing.narratives else None
    )
    audit.is_new = landing.is_new
    # `continuity_ms` / `judge_ms` stay NULL: those tiers did not run, and a
    # 0 there would read as "the tier is free" — the opposite of true. The
    # merged call's own cost is `merged_ms`, which nests nothing.
    await service._write_audit(audit, snapshots)

    return NarrativeSelectionResult(
        narratives=landing.narratives,
        selection_reason=landing.reason,
        selection_method=landing.method,
        no_durable_topic=landing.no_durable_topic,
        is_new=landing.is_new,
        best_score=None,
        retrieval_method=landing.retrieval_method,
    )


async def _land(
    service: "NarrativeService",
    *,
    decision: MergedRoutingDecision,
    routing_input: MergedRoutingInput,
    menu_results,
    anchor: Optional[Narrative],
    continuable: bool,
    session: Optional[ConversationSession],
    agent_id: str,
    user_id: str,
    query_text: str,
    max_narratives: int,
) -> Landing:
    """Dispatch one decision to its landing. Each branch is a whole Landing."""
    if not decision.ok:
        return await _land_failure(
            service, anchor=anchor, continuable=continuable,
            reason=decision.reason, agent_id=agent_id, user_id=user_id,
            query_text=query_text,
        )
    if decision.verdict == VERDICT_CONTINUE_ANCHOR:
        return Landing(
            narratives=[anchor] if anchor else [],
            method="merged_continue",
            reason=f"Continued the anchored thread: {decision.reason}",
            retrieval_method="session",
        )
    if decision.verdict == VERDICT_MATCH:
        chosen_id = resolve_choice(decision, routing_input)
        # The judge's own landing, called rather than copied. The trailing
        # rows are context for the agent prompt and come from the menu: the
        # anchored thread the model just left is deliberately not re-appended
        # as context for leaving it.
        return Landing(
            narratives=await service._retrieval.assemble_match_landing(
                chosen_id or "", menu_results, max_narratives
            ),
            method="merged_match",
            reason=f"Switched to an existing thread: {decision.reason}",
            retrieval_method="keyword",
        )
    if decision.verdict == VERDICT_PARTICIPANT:
        chosen_id = resolve_choice(decision, routing_input)
        return Landing(
            narratives=await service._retrieval.load_participant_landing(
                chosen_id
            ),
            method="merged_participant",
            reason=f"Matched a thread the user participates in: {decision.reason}",
            retrieval_method="keyword",
        )
    if decision.verdict == VERDICT_NEW:
        created = await service._retrieval.create_from_query(
            query=query_text, user_id=user_id, agent_id=agent_id,
            narrative_type=NarrativeType.CHAT,
        )
        return Landing(
            narratives=[created],
            method="merged_new",
            reason=f"A new subject: {decision.reason}",
            retrieval_method="keyword",
            is_new=True,
        )
    assert decision.verdict == VERDICT_NO_TOPIC, decision.verdict
    # The verdict carries no destination; `_land_no_topic_turn` owns that,
    # anchor-first, and its freeze semantics are untouched — a greeting must
    # never rename the work it interrupted.
    return await service._land_no_topic_turn(
        agent_id=agent_id, user_id=user_id, query_text=query_text,
        session=session, reason=decision.reason,
    )


async def _land_failure(
    service: "NarrativeService",
    *,
    anchor: Optional[Narrative],
    continuable: bool,
    reason: str,
    agent_id: str,
    user_id: str,
    query_text: str,
) -> Landing:
    """RULE 6 — a failure is not a verdict.

    Two production incidents (D19) had this exact shape: the deciding tier
    failed, the failure fell through to creation, the created thread became
    the anchor, and the updater rewrote it until the lexical evidence agreed.
    So: stay where we already were, flagged; and where there is nowhere to
    stay, create — but never silently, never as a switch.
    """
    if anchor is not None and continuable:
        return Landing(
            narratives=[anchor],
            method="merged_fallback_anchor",
            reason=(
                f"Merged routing unavailable, held the anchored thread: {reason}"
            ),
            retrieval_method="session",
        )
    created = await service._retrieval.create_from_query(
        query=query_text, user_id=user_id, agent_id=agent_id,
        narrative_type=NarrativeType.CHAT,
    )
    return Landing(
        narratives=[created],
        method="merged_fallback_new",
        reason=(
            f"Merged routing unavailable and no thread to hold the turn: {reason}"
        ),
        retrieval_method="keyword",
        is_new=True,
    )
