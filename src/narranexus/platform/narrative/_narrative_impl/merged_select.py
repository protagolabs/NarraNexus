"""
@file_name: merged_select.py
@author: NetMind.AI
@date: 2026-08-27
@description: Orchestration of the merged routing path — one decision per
              turn: BM25 first, then a shutter or ONE decision-model call.

Moved out of NarrativeService on review (2026-08-27, Important #2): the
service file had grown past the 800-line bound with two 250-line deciders
side by side, and every verdict branch hand-assigned six loose locals. Here
each landing is a small function returning a frozen ``Landing`` (defined in
landings.py so the flag-off path can use it without importing this module's
decision-client chain) — six fields constructed at once, so a new
``NarrativeSelectionResult`` field cannot be silently defaulted on one
verdict and set on another.

The service keeps a thin ``_select_merged`` delegate; ``is_reusable_anchor``
and ``minutes_since`` stay ONE definition each (anchor_rules.py), shared with
``select()``, ``_land_no_topic_turn`` and ``step_1_fast_select``.
"""

from __future__ import annotations

from typing import Optional, Sequence, TYPE_CHECKING

from loguru import logger

from narranexus.platform.agent_framework.llm.decision_model import DecisionEndpoint

from ..config import config
from ..models import (
    ConversationSession,
    NarrativeSearchResult,
    Narrative,
    NarrativeSelectionResult,
    NarrativeType,
    RoutingAudit,
)
from .anchor_rules import (
    advance_session_anchor,
    is_reusable_anchor,
    minutes_since,
)
from .landings import (
    Landing,
    land_no_topic,
    assemble_match_landing,
    build_menu_candidates,
    build_participant_candidates,
    load_participant_landing,
)
from .merged_prep import prepare_merged_routing
from .merged_router import (
    MergedRoutingDecision,
    MergedRoutingInput,
    VERDICT_CONTINUE_ANCHOR,
    VERDICT_MATCH,
    VERDICT_NEW,
    VERDICT_NO_TOPIC,
    VERDICT_PARTICIPANT,
    decide,
    resolve_choice,
)
from .routing_gate import pick_menu

if TYPE_CHECKING:
    from .crud import NarrativeCRUD
    from .retrieval import NarrativeRetrieval


async def select_merged(
    crud: "NarrativeCRUD",
    retrieval: "NarrativeRetrieval",
    write_audit,
    *,
    agent_id: str,
    user_id: str,
    query_text: str,
    max_narratives: int,
    session: Optional[ConversationSession],
    decision_endpoint: DecisionEndpoint,
    is_user_chat: bool,
    trigger: str,
) -> NarrativeSelectionResult:
    """One decision per turn: BM25 first, then a shutter or ONE decision call.

    The decider changes, the executors do not: every landing below is the
    two-call path's own code (`assemble_match_landing`,
    `load_participant_landing`, `create_from_query`, `land_no_topic`), and
    downstream reads `narratives` / `is_new` / `no_durable_topic` /
    `retrieval_method` without branching on `selection_method`.

    ``decision_endpoint`` is the credential resolved for THIS turn by the
    caller; a turn without one never reaches this function.

    `anchor_bm25_rank` / `anchor_in_menu` are recorded on every row: merging
    lets foreign threads into a decision the two-call path never built on
    continuity turns, and these columns measure how often the unconditional
    anchor question is what kept the anchor on the ballot.
    """
    from narranexus.platform.utils.logging import timed

    audit = RoutingAudit(
        agent_id=agent_id, user_id=user_id, query_text=query_text,
        trigger=trigger, is_user_chat=is_user_chat,
    )
    # The PATH, not the call: a turn the shutter releases took this path and
    # asked nobody.
    audit.merged_call = True
    snapshots: dict = {}

    anchor: Optional[Narrative] = None
    anchor_id = session.current_narrative_id if session else None
    if anchor_id:
        anchor = await crud.load_by_id(anchor_id)
    # THE one definition, shared with the fast path and the no-topic
    # landing: a legacy default bucket is a verdict about an earlier turn,
    # not a thread anyone may continue.
    continuable = is_reusable_anchor(anchor)

    with timed("narrative.merged.prepare"):
        prep = await prepare_merged_routing(
            retrieval,
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
        # One cut, here: what enters the input is exactly what gets a
        # question, so every index the verdict can name is on the ballot.
        # Prefix slice only — the ORDER is the P0-4 priority rule.
        # The anchor is filtered out of the participant list (after a
        # participant landing it is both), or a "stay" would be asked twice
        # and audited as a switch. Only this copy is filtered:
        # prep.participant_narratives feeds the shutter's participant rule.
        participant_pool = [
            n for n in prep.participant_narratives
            if anchor is None or n.id != anchor.id
        ]
        all_participants = build_participant_candidates(participant_pool)
        shown_participants = all_participants[
            : config.MERGED_PARTICIPANT_MAX_CANDIDATES
        ]
        participants_cut = len(all_participants) > len(shown_participants)
        routing_input = MergedRoutingInput(
            query=query_text,
            anchor=anchor,
            anchor_is_continuable=bool(anchor is not None and continuable),
            previous_query=(session.last_query or "") if session else "",
            previous_response=(session.last_response or "") if session else "",
            minutes_since_previous=minutes_since(session),
            menu=await build_menu_candidates(crud, menu_results),
            participants=shown_participants,
        )

        with timed("narrative.merged.decide") as t:
            decision = await decide(routing_input, decision_endpoint)
            # No helper LLM runs here, so the helper-call tag would name a
            # stale model; tag from the decision model's own answer. Its token
            # usage is logged here, not booked to cost_records: that ledger
            # attributes rows to the user's helper provider card, which did
            # not pay for this call.
            if decision.result is not None:
                t.tag(
                    model=decision.result.model, structured="decision",
                    input_tokens=decision.result.input_tokens,
                    output_tokens=decision.result.output_tokens,
                )

        audit.merged_verdict = decision.verdict
        audit.merged_ms = decision.elapsed_ms
        if decision.request is not None:
            audit.merged_input_chars = decision.request.input_chars
            truncated = list(decision.request.truncated)
            # The participant cut happens above, before request assembly, so
            # its flag is raised here.
            if participants_cut and "participants" not in truncated:
                truncated.append("participants")
            audit.merged_truncated = ",".join(truncated)

        # The landing pool is the full ranking, not the ballot: the menu knob
        # must not shrink the ChatModule's trailing context. The anchor stays
        # out — the verdict just left it.
        landing_pool = [
            r for r in prep.ranked
            if anchor is None or r.narrative_id != anchor.id
        ]
        landing = await _land(
            crud, retrieval,
            decision=decision,
            routing_input=routing_input,
            landing_pool=landing_pool,
            anchor=anchor,
            continuable=continuable,
            session=session,
            agent_id=agent_id,
            user_id=user_id,
            query_text=query_text,
            max_narratives=max_narratives,
        )

    advance_session_anchor(session, query_text, landing.narratives, is_user_chat)

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
    await write_audit(audit, snapshots)

    return NarrativeSelectionResult(
        narratives=landing.narratives,
        selection_reason=landing.reason,
        selection_method=landing.method,
        no_durable_topic=landing.no_durable_topic,
        is_new=landing.is_new,
        # Empty like the two-call path: select() drops retrieval_result's
        # best_score/scores at its own exit, so the panel's score labels are
        # absent on BOTH arms. Round 6's review claimed the two-call path
        # fills them — it does not (round 8, I2, verified at the select()
        # return) — and matching a wrong claim had INVERTED the difference.
        best_score=None,
        retrieval_method=landing.retrieval_method,
    )


async def _land(
    crud: "NarrativeCRUD",
    retrieval: "NarrativeRetrieval",
    *,
    decision: MergedRoutingDecision,
    routing_input: MergedRoutingInput,
    landing_pool: Sequence[NarrativeSearchResult],
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
            retrieval, anchor=anchor, continuable=continuable,
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
        # The judge's own landing, called rather than copied. Trailing rows
        # come from the FULL ranking (anchor excluded), so the context depth
        # answers to MAX_NARRATIVES_IN_CONTEXT alone — never to the menu knob.
        return Landing(
            narratives=await assemble_match_landing(
                crud, chosen_id or "", landing_pool, max_narratives
            ),
            method="merged_match",
            reason=f"Switched to an existing thread: {decision.reason}",
            retrieval_method="keyword",
        )
    if decision.verdict == VERDICT_PARTICIPANT:
        chosen_id = resolve_choice(decision, routing_input)
        return Landing(
            narratives=await load_participant_landing(crud, chosen_id),
            method="merged_participant",
            reason=f"Matched a thread the user participates in: {decision.reason}",
            retrieval_method="keyword",
        )
    if decision.verdict == VERDICT_NEW:
        created = await retrieval.create_from_query(
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
    if decision.verdict == VERDICT_NO_TOPIC:
        # The verdict carries no destination; `_land_no_topic_turn` owns that,
        # anchor-first, and its freeze semantics are untouched — a greeting
        # must never rename the work it interrupted.
        return await land_no_topic(
            crud, retrieval,
            agent_id=agent_id, user_id=user_id, query_text=query_text,
            session=session, reason=decision.reason, anchor=anchor,
        )
    # Unreachable while decide() validates verdicts — but an assert would be
    # stripped under python -O and fall through to None (review round 4, M6),
    # so the honest terminal branch for a verdict that got past validation is
    # the same one every other failure takes.
    return await _land_failure(
        retrieval, anchor=anchor, continuable=continuable,
        reason=f"unhandled verdict: {decision.verdict}",
        agent_id=agent_id, user_id=user_id, query_text=query_text,
    )


async def _land_failure(
    retrieval: "NarrativeRetrieval",
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
    created = await retrieval.create_from_query(
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
