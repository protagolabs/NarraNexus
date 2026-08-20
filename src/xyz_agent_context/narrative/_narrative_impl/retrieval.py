"""
Narrative retrieval implementation

@file_name: retrieval.py
@author: NetMind.AI
@date: 2025-12-22
@description: BM25 keyword retrieval + LLM unified match for narrative routing.
"""

from __future__ import annotations

import asyncio
import time as _perf
from typing import List, Optional, Tuple, TYPE_CHECKING

from loguru import logger

from ..config import config
from ..models import (
    Narrative,
    NarrativeSearchResult,
    NarrativeSelectionResult,
    NarrativeType,
    RoutingAudit,
    RoutingCandidate,
)
from .crud import NarrativeCRUD
from .routing_gate import evaluate_bypass, evaluate_gate
from .default_narratives import (
    DEFAULT_NARRATIVES_CONFIG,
    ensure_default_narratives,
    build_default_narrative_id_pattern,
)
from xyz_agent_context.utils.logging import timed

# Use common utilities from utils
from xyz_agent_context.utils.text import (
    extract_keywords,
    strip_routing_prefix,
    truncate_text,
)
from xyz_agent_context.utils.db.db_factory import get_db_client
from ._retrieval_llm import llm_judge_unified

if TYPE_CHECKING:
    from xyz_agent_context.utils.db.database import AsyncDatabaseClient
    from xyz_agent_context.repository import NarrativeRepository

# How much of a candidate reaches the judge. The judge prompt is rebuilt from
# scratch on every crowded turn, so each of these is paid per candidate per
# judged turn — hence bounded here rather than "whatever fits".
MAX_MATCHED_TERMS = 5  # Terms shown per candidate, highest contribution first
CANDIDATE_DESC_MAX_CHARS = 300  # Summary excerpt shown per candidate


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


class NarrativeRetrieval:
    """
    Narrative Retrieval

    Responsibilities:
    - BM25 keyword search over the agent's narratives
    - LLM unified-match confirmation / new-narrative creation
    """

    def __init__(self, agent_id: str):
        """
        Initialize retrieval engine

        Args:
            agent_id: Agent ID
        """
        self.agent_id = agent_id
        self._crud = NarrativeCRUD(agent_id)
        self._event_service = None  # Dependency injection

    def set_database_client(self, db_client: "AsyncDatabaseClient"):
        """Set the database client"""
        self._crud.set_database_client(db_client)

    def set_event_service(self, event_service):
        """Inject EventService"""
        self._event_service = event_service

    async def retrieve_top_k(
        self,
        query: str,
        user_id: str,
        agent_id: str,
        top_k: int,
        narrative_type: NarrativeType = NarrativeType.CHAT,
        *,
        anchor_narrative_id: Optional[str] = None,
        is_user_chat: bool = True,
    ) -> NarrativeSelectionResult:
        """Retrieve Top-K Narratives, and record the evidence behind the choice.

        Thin wrapper over ``_retrieve_top_k``: it owns the RoutingAudit so the
        outcome is stamped in ONE place. The inner method has seven return
        points across itself and ``_llm_unified_match``; filling the audit at
        each of them is exactly the kind of bookkeeping that rots — one new
        branch later and that path silently stops being observable, which is
        the failure mode this whole table exists to end.

        The audit rides on the returned NarrativeSelectionResult;
        ``NarrativeService.select`` adds the continuity half and writes it.
        """
        audit = RoutingAudit(
            agent_id=agent_id, user_id=user_id, query_text=query,
        )
        snapshots: dict = {}
        result = await self._retrieve_top_k(
            query, user_id, agent_id, top_k, narrative_type, audit, snapshots,
            anchor_narrative_id=anchor_narrative_id,
            is_user_chat=is_user_chat,
        )
        audit.selection_method = result.selection_method
        audit.retrieval_method = result.retrieval_method
        audit.chosen_narrative_id = result.narratives[0].id if result.narratives else None
        audit.is_new = result.is_new
        result.audit = audit
        result.audit_snapshots = snapshots
        return result

    async def _retrieve_top_k(
        self,
        query: str,
        user_id: str,
        agent_id: str,
        top_k: int,
        narrative_type: NarrativeType,
        audit: "RoutingAudit",
        snapshots: dict,
        *,
        anchor_narrative_id: Optional[str],
        is_user_chat: bool,
    ) -> NarrativeSelectionResult:
        """
        Retrieve Top-K Narratives (two-tier threshold + LLM unified judgment)

        Workflow:
        0. Ensure default Narratives exist
        1. BM25 keyword search over the agent's narratives (name + summary +
           topic_keywords); add PARTICIPANT narratives at a neutral score
        2. Two-tier threshold judgment:
           a) High confidence (>= high threshold) -> Return Top-K directly
           b) Low confidence (< high threshold) -> LLM unified judgment (search results + default Narratives)
              - Match default type -> Return 1 default Narrative
              - Match search result -> Return Top-K list
              - No match -> Create new Narrative

        Args:
            query: User query
            user_id: User ID
            agent_id: Agent ID
            top_k: Number of results to return
            narrative_type: Narrative type

        Returns:
            NarrativeSelectionResult: Contains Narrative list, selection reason, and other complete info
        """
        logger.info(f"Retrieving Top-{top_k} Narratives: query='{query[:50]}...'")

        # Step 0: Ensure default Narratives exist.
        # Must stay AHEAD of the pool load below and cannot join the gather:
        # it CREATES default narratives when they are missing, and a pool read
        # racing that creation would silently omit them from the BM25 candidate
        # set — a wrong answer, not a slow one.
        with timed("narrative.retrieve.ensure_defaults"):
            await self._ensure_default_narratives(agent_id, user_id)

        # Step 0.5 (P0-4): Narratives where the user is a PARTICIPANT, and
        # Step 1's candidate pool. Two independent reads — neither feeds the
        # other — so they overlap.
        #
        # Honest accounting: measured on a live instance these are ~3ms and
        # ~5ms, against a setup phase whose p50 is 8.5 SECONDS. This saves
        # single-digit milliseconds and is emphatically NOT the fix for
        # narrative-selection latency. The cost is the two helper-LLM round
        # trips either side of it (continuity ~3.9s, unified judge ~4.7s), and
        # anyone arriving here to make selection faster should go there — see
        # the `continuity_ms` / `judge_ms` columns on narrative_routing_audit,
        # which exist to make that obvious without re-deriving it.
        async def _load_pool_timed():
            """`load_pool` plus its own elapsed ms.

            Timed separately because the two reads now overlap: the enclosing
            span measures max(participant, pool), which is the right number for
            "how long did this step take" and the WRONG one for `keyword_ms`,
            whose documented meaning is "BM25 pool load + rank". Without this,
            a slow participant query would be charged to BM25.
            """
            _t0 = _perf.monotonic()
            result = await self.load_pool(agent_id, user_id)
            return result, int((_perf.monotonic() - _t0) * 1000)

        # Named for what it measures. It was `narrative.retrieve.participant_query`
        # while wrapping only that query; once the pool read joined it, the old
        # name would have made a cross-PR comparison of `[TIMED]` history read a
        # scope change as a performance change — using the very method this
        # branch demonstrates.
        with timed("narrative.retrieve.independent_reads"):
            # Coroutines straight into gather — no `create_task` wrapper.
            # gather already schedules them concurrently, and holding our own
            # Task handles would only add two objects for nothing.
            #
            # `return_exceptions` stays at its default False: either read
            # failing means the candidate set is INCOMPLETE, and routing
            # confidently on the remainder would be a wrong answer dressed as
            # a right one. The caller must see it. (Note: gather does NOT
            # cancel the sibling on first exception — the surviving read runs
            # to completion with nobody to receive it. Harmless here, one
            # wasted DB round trip.)
            participant_narratives, (pool, _pool_ms) = await asyncio.gather(
                self._get_participant_narratives(user_id=user_id, agent_id=agent_id),
                _load_pool_timed(),
            )
        has_participant_narratives = len(participant_narratives) > 0
        if has_participant_narratives:
            logger.info(f"P0-4: User is a PARTICIPANT in {len(participant_narratives)} Narratives")

        # Step 1: Search for candidate Narratives by KEYWORD (BM25 over each
        # narrative's name + summary + topic keywords). BM25 casts the net
        # over the agent's real narratives — including non-default ones — then
        # the LLM unified-match tier below arbitrates. Reuses the same BM25 the
        # MemoryEngine uses, so narrative routing and memory recall share one
        # ranking implementation. Zero vectors.
        with timed("narrative.retrieve.keyword_search"):
            # `pool` was loaded above, alongside the participant query.
            # rank_pool rather than keyword_search: the audit needs the WHOLE
            # pool with the exact text that was scored, and BM25's IDF/avgdl
            # are computed over that set, so a top-K slice cannot be replayed.
            # keyword_search stays the public seam for select_fast.
            _t_rank = _perf.monotonic()
            search_results = self.rank_pool(
                query, pool, max(top_k * 2, config.NARRATIVE_SEARCH_TOP_K)
            )
            _rank_ms = int((_perf.monotonic() - _t_rank) * 1000)
        # Pool read + ranking, and nothing else — the participant query it now
        # runs alongside is a different question and must not be charged here.
        # This column answers "is BM25 ever the problem?"; mixing in an
        # unrelated read is how it would answer wrongly.
        audit.keyword_ms = _pool_ms + _rank_ms
        retrieval_method = "keyword"
        logger.info(f"[NarrativeSelect] Keyword(BM25) search returned {len(search_results)} candidates")

        # Step 1.5 (P0-4): Add PARTICIPANT Narratives to candidate list (if not already in search results)
        # This is key: participant_narratives come from Narratives created by other users; keyword search won't return them
        existing_narrative_ids = {r.narrative_id for r in search_results}
        for narrative in participant_narratives:
            if narrative.id not in existing_narrative_ids:
                # Embeddings retired: participant narratives enter the candidate
                # pool with a neutral score; the LLM unified-match tier below
                # arbitrates their relevance. (No cosine scoring.)
                search_results.append(NarrativeSearchResult(
                    narrative_id=narrative.id,
                    similarity_score=0.5,
                    rank=999
                ))
                logger.info(f"  Added PARTICIPANT Narrative: {narrative.id} (neutral score 0.5)")

        # Re-sort (by similarity descending) and update rank
        search_results.sort(key=lambda x: x.similarity_score, reverse=True)
        for i, result in enumerate(search_results):
            result.rank = i + 1

        # Freeze the candidate set AFTER the participant merge, not before:
        # participant narratives are appended post-ranking with a synthetic
        # neutral score, so recording earlier would drop them entirely and
        # leave `is_participant` permanently false — losing exactly the
        # candidates the P0-4 priority rule is about.
        self._record_pool(audit, snapshots, pool, search_results, participant_narratives)

        # Step 2: Two-tier threshold judgment
        best_score = search_results[0].similarity_score if search_results else None
        all_scores = {r.narrative_id: r.similarity_score for r in search_results}

        # First tier: high confidence - return Top-K directly.
        # The gate reads RAW BM25, not the squashed similarity — see
        # routing_gate.evaluate_gate for why. Participant narratives still
        # force LLM judgment regardless: they carry a synthetic neutral score,
        # and a high BM25 hit on the user's OWN narrative should not win over
        # the task they were invited into (P0-4).
        gate = evaluate_gate(
            [r.raw_score for r in search_results],
            raw_floor=config.NARRATIVE_MATCH_RAW_FLOOR,
            margin_ratio=config.NARRATIVE_MATCH_MARGIN_RATIO,
        )
        # Which narrative BM25 actually wants. NOT `search_results[0]`: the
        # participant merge above appends entries with a synthetic 0.5
        # similarity and re-sorts on that, so position 0 can be a narrative
        # that never went through bm25 at all. The bypass rule has to compare
        # the KEYWORD winner against the anchor or it would compare noise.
        keyword_leader = max(
            search_results, key=lambda r: r.raw_score, default=None
        )
        top1_narrative_id = (
            keyword_leader.narrative_id
            if keyword_leader is not None and keyword_leader.raw_score > 0
            else None
        )
        # Second decision, separate from strength: may this turn skip review at
        # all? A bypass is only ever allowed to KEEP a turn where it already
        # was — see routing_gate.evaluate_bypass for the prod measurement
        # behind that (92.5% of bypasses were already doing exactly that, and
        # all of the hijack risk lives in the other 7.5%).
        bypass = evaluate_bypass(
            gate,
            top1_narrative_id=top1_narrative_id,
            anchor_narrative_id=anchor_narrative_id,
            is_user_chat=is_user_chat,
            has_participant_narratives=has_participant_narratives,
        )
        # `gate_short_circuit` keeps its original meaning — "this turn skipped
        # the judge" — so it now reflects the bypass decision, not floor+margin.
        # `bypass_score_gate` is what preserves the floor/margin series for the
        # next calibration round.
        audit.gate_short_circuit = bypass.granted
        audit.bypass_score_gate = gate.short_circuit
        audit.bypass_reason = bypass.reason
        audit.gate_reason = bypass.detail
        audit.gate_top1_raw = gate.top1_raw
        audit.gate_top2_raw = gate.top2_raw
        # inf is not JSON/DOUBLE-safe; a lone candidate has an unbounded margin
        audit.gate_margin = gate.margin if gate.margin != float("inf") else None
        if bypass.granted:
            logger.info(f"[NarrativeSelect] high confidence — {bypass.detail}")
            narratives = []
            for result in search_results[:top_k]:
                narrative = await self._crud.load_by_id(result.narrative_id)
                if narrative:
                    narratives.append(narrative)

            return NarrativeSelectionResult(
                narratives=narratives,
                selection_reason=f"High confidence match: {bypass.detail}",
                selection_method="high_confidence",
                is_new=False,
                best_score=best_score,
                scores=all_scores,
                retrieval_method=retrieval_method,
                # evermemos_memories removed — EverMemOS decoupled from narrative selection
            )

        if search_results:
            logger.info(
                f"[NarrativeSelect] deferring to LLM ({bypass.reason}) — "
                f"{bypass.detail}"
            )

        # P0-4: If user has PARTICIPANT Narratives, force LLM judgment
        if has_participant_narratives:
            logger.info(f"User has PARTICIPANT Narratives, forcing LLM judgment (best_score={f'{best_score:.2f}' if best_score else 'N/A'})")

        # Second tier: Low confidence - LLM unified judgment
        logger.info(f"Low confidence (score={best_score if best_score else 'N/A'}), using LLM unified judgment...")

        if config.NARRATIVE_MATCH_USE_LLM:
            # Call unified LLM judgment (considers search results, default Narratives, and PARTICIPANT Narratives)
            # This is the slow path — wrap in timed() so the dual cost
            # (LLM call + extra DB loads inside _llm_unified_match) is
            # visible separately from the BM25 keyword search above.
            _t_judge = _perf.monotonic()
            with timed("narrative.retrieve.llm_unified_match") as t:
                result = await self._llm_unified_match(
                    query=query,
                    search_results=search_results[:3] if search_results else [],
                    agent_id=agent_id,
                    user_id=user_id,
                    top_k=top_k,
                    narrative_type=narrative_type,
                    best_score=best_score,
                    participant_narratives=participant_narratives,  # P0-4: Pass PARTICIPANT Narratives
                    retrieval_method=retrieval_method,  # Pass retrieval method
                    audit=audit,
                )
                # Tag with the model + structured-output mode the SDK
                # ended up using inside _llm_unified_match → llm_judge_unified
                # → sdk.llm_function. See adapters.openai_agents.get_last_llm_call_info.
                from xyz_agent_context.agent_framework.adapters.openai_agents import (
                    get_last_llm_call_info,
                )
                info = get_last_llm_call_info()
                if info:
                    t.tag(**info)
                # Set before returning, not after: this is the only exit from
                # the judged path, and `retrieve_top_k` stamps the outcome onto
                # the same audit object afterwards.
                audit.judge_ms = int((_perf.monotonic() - _t_judge) * 1000)
                return result

        # LLM not enabled - Create new Narrative directly
        else:
            logger.info("LLM not enabled, creating new topic directly")
            new_narrative = await self.create_from_query(
                query=query,
                user_id=user_id,
                agent_id=agent_id,
                narrative_type=narrative_type
            )

            return NarrativeSelectionResult(
                narratives=[new_narrative],
                selection_reason="LLM not enabled, created new topic directly",
                selection_method="new_created",
                is_new=True,
                best_score=best_score,
                scores=all_scores,
                retrieval_method=retrieval_method,
                # evermemos_memories removed — EverMemOS decoupled from narrative selection
            )

    async def _ensure_default_narratives(self, agent_id: str, user_id: str) -> None:
        """
        Ensure default Narratives exist for the agent-user combination

        Uses NarrativeRepository.count_default_narratives() method for checking,
        avoiding direct SQL in business logic.

        Check logic:
        1. Use Repository to query default Narrative count
        2. If exists, return directly (already initialized)
        3. If not exists, call ensure_default_narratives to create

        Args:
            agent_id: Agent ID
            user_id: User ID
        """
        # C-1: buckets are no longer routing containers, so a new (agent,user)
        # pair must not acquire eight of them. This is the SEEDING half of the
        # change; existing rows are left untouched (binding rule #6) and simply
        # stop being loaded into the pool above.
        if not config.NARRATIVE_DEFAULT_BUCKETS_ENABLED:
            logger.debug(
                "Default buckets disabled — skipping seeding for "
                f"agent {agent_id} + user {user_id}"
            )
            return

        # Use Repository to check if default Narratives already exist (lazy import to avoid circular dependency)
        from xyz_agent_context.repository import NarrativeRepository
        db_client = await get_db_client()
        repo = NarrativeRepository(db_client)

        count = await repo.count_default_narratives(agent_id, user_id)

        if count > 0:
            # Default Narratives already exist
            logger.debug(
                f"Default Narratives for Agent {agent_id} + User {user_id} already exist "
                f"({count} found)"
            )
            return

        # Do not exist, need to create
        logger.info(
            f"Default Narratives for Agent {agent_id} + User {user_id} do not exist, creating..."
        )

        try:
            default_narratives = await ensure_default_narratives(
                agent_id=agent_id,
                user_id=user_id,
                crud=self._crud  # Pass crud instance to avoid circular dependency
            )

            logger.info(
                f"Successfully created {len(default_narratives)} default Narratives "
                f"for Agent {agent_id} + User {user_id}"
            )
        except Exception as e:
            logger.exception(
                f"Failed to create default Narratives (agent={agent_id}, user={user_id}): {e}"
            )
            # Do not raise exception, allow continued execution (default Narrative creation failure should not block main flow)

    @classmethod
    def _record_pool(
        cls,
        audit: "RoutingAudit",
        snapshots: dict,
        pool: List[Tuple[str, str, bool]],
        search_results: List[NarrativeSearchResult],
        participant_narratives: Optional[List[Narrative]] = None,
    ) -> None:
        """Freeze the candidate set into the audit, with the text that was scored.

        Every BM25 pool member is recorded, including the ones that scored
        zero — they still shaped the ranking through IDF and avgdl, so a
        replay that omits them reproduces different numbers.

        Participant narratives are recorded too but are NOT part of the BM25
        pool: they are appended after ranking with a synthetic neutral score
        and never went through bm25_rank. `raw_score` stays 0.0 for them,
        matching what the gate sees (NarrativeSearchResult.raw_score default),
        so a replay does not mistake them for keyword hits.
        """
        from xyz_agent_context.repository.narrative_routing_audit_repository import (
            text_hash,
        )

        scored = {r.narrative_id: r.raw_score for r in search_results}
        participants = {n.id: n for n in (participant_narratives or [])}
        seen: set = set()

        for nid, text, is_default in pool:
            h = text_hash(text)
            snapshots[h] = text
            seen.add(nid)
            audit.candidates.append(RoutingCandidate(
                narrative_id=nid,
                text_hash=h,
                raw_score=scored.get(nid, 0.0),
                is_default=is_default,
                is_participant=nid in participants,
            ))

        for nid, narrative in participants.items():
            if nid in seen:
                continue
            text = narrative.searchable_text()
            h = text_hash(text)
            snapshots[h] = text
            audit.candidates.append(RoutingCandidate(
                narrative_id=nid,
                text_hash=h,
                raw_score=0.0,
                is_default=narrative.is_special == "default",
                is_participant=True,
            ))

    async def load_pool(
        self,
        agent_id: str,
        user_id: str,
    ) -> List[Tuple[str, str, bool]]:
        """The BM25 candidate pool: (narrative_id, scored_text, is_default).

        Split out of ``keyword_search`` so the routing audit can persist the
        WHOLE pool with the exact text that was scored. That completeness is
        load-bearing, not defensive: ``bm25_rank`` computes IDF and avgdl over
        the set it is handed, so a candidate's score depends on every other
        document present — including the eight default narratives, which are
        semantically irrelevant yet moved top-1 on 9.7% of 452 replayed local
        queries (2026-08-07). Re-deriving the pool later cannot work either:
        the scored text is rewritten wholesale by the async LLM updater with
        no history kept.
        """
        narratives = await self._crud.load_by_agent_user(agent_id, user_id, limit=100)
        keep_buckets = config.NARRATIVE_DEFAULT_BUCKETS_ENABLED
        return [
            (n.id, n.searchable_text(), n.is_special == "default")
            for n in narratives
            # C-1: a bucket's searchable_text is a frozen factory template, so
            # it can never legitimately WIN a query — but it still shifts every
            # other candidate's score through IDF/avgdl, and it can short-
            # circuit the gate on its own (measured: 2 turns in the replay).
            # Dropping it from the pool is what makes the rest of the batch
            # honest. Existing rows stay in the DB; only routing stops seeing
            # them.
            if keep_buckets or n.is_special != "default"
        ]

    @staticmethod
    def rank_pool(
        query: str,
        pool: List[Tuple[str, str, bool]],
        top_k: int,
    ) -> List[NarrativeSearchResult]:
        """Rank an already-loaded pool. Pure — no DB, so the audit replay and
        the live decision run byte-identical code.

        Ranks via ``bm25_explain`` rather than ``bm25_rank``: same arithmetic
        and the same score to the last bit, but it also hands back WHICH query
        terms earned that score. That evidence travels to the LLM judge
        (`_llm_unified_match`), and this is the only moment it is free — the
        scored text is in hand here, and reconstructing it later is impossible
        because the async updater rewrites it wholesale with no history.

        The routing prefix is stripped HERE, not at the call sites, so every
        BM25 consumer inherits it — `retrieve_top_k`, `keyword_search`, and
        `select_fast` through it — and so a replay that goes through this
        method still reproduces the live decision byte for byte. The audit row
        keeps the ORIGINAL query text: what the user said is the record, and
        what BM25 scored is derivable from it by this same function.
        """
        from xyz_agent_context.memory._memory_impl.retrieval import (
            bm25_explain,
            bm25_snippet,
        )

        # "[From <sender>] " is routing metadata, not topic evidence: it is
        # present on 96% of prod queries and carries 100% of the score on the
        # worst of them (audit 768, `[From Liam] 👊` -> 5.66 from `from`+`liam`
        # alone, the emoji tokenising to nothing). The judge and the continuity
        # tier still read the untouched text — they can tell a name from a
        # topic, BM25 cannot. See utils.text.strip_routing_prefix.
        explained = bm25_explain(
            strip_routing_prefix(query), [(nid, text) for nid, text, _ in pool]
        )
        texts = {nid: text for nid, text, _ in pool}
        ranked = sorted(
            explained.items(), key=lambda kv: kv[1][0], reverse=True
        )[:top_k]
        results = []
        for i, (nid, (score, contributions)) in enumerate(ranked):
            terms = [term for term, _ in contributions]
            results.append(NarrativeSearchResult(
                narrative_id=nid,
                similarity_score=score / (score + 1.0),
                rank=i + 1,
                raw_score=score,
                matched_terms=terms[:MAX_MATCHED_TERMS],
                matched_snippet=bm25_snippet(texts[nid], terms),
            ))
        return results

    async def keyword_search(
        self,
        query: str,
        user_id: str,
        agent_id: str,
        top_k: int,
    ) -> List[NarrativeSearchResult]:
        """BM25 keyword retrieval over the agent's narratives — the non-vector
        BM25 keyword search over the agent's narratives.

        Ranks each narrative by query overlap on its name + current_summary +
        description + topic_keywords, using the same BM25 the MemoryEngine uses.
        Scores are normalized monotonically into (0,1) so the existing two-tier
        threshold still applies: weak matches fall through to the LLM tier;
        strong keyword matches may direct-return.

        Public seam: ``NarrativeService.select_fast`` (F28) depends on this
        signature. ``retrieve_top_k`` uses ``load_pool`` + ``rank_pool``
        directly so it can keep the pool for the audit.
        """
        return self.rank_pool(query, await self.load_pool(agent_id, user_id), top_k)

    async def _llm_unified_match(
        self,
        query: str,
        search_results: List[NarrativeSearchResult],
        agent_id: str,
        user_id: str,
        top_k: int,
        narrative_type: NarrativeType,
        best_score: Optional[float],
        participant_narratives: Optional[List[Narrative]] = None,  # P0-4: PARTICIPANT Narratives
        retrieval_method: str = "",  # Retrieval method identifier
        audit: Optional["RoutingAudit"] = None,  # E1: filled in place with the judge verdict
    ) -> NarrativeSelectionResult:
        """
        LLM unified judgment: Considers search results, default Narratives, and PARTICIPANT Narratives

        Uses NarrativeRepository.get_default_narratives() method to get default Narratives,
        avoiding direct SQL in business logic.

        Workflow:
        1. Load searched Narratives and default Narratives
        2. (P0-4) Load PARTICIPANT Narratives (topics where user is a PARTICIPANT)
        3. Call LLM to determine which one the user query matches
        4. Based on match result:
           a) Match PARTICIPANT -> Return with priority (PARTICIPANT task priority)
           b) Match default type -> Return 1 default Narrative
           c) Match search result -> Return Top-K list
           d) No match -> Create new Narrative

        Args:
            query: User query
            search_results: BM25 keyword search results
            agent_id: Agent ID
            user_id: User ID
            top_k: Number of results to return
            narrative_type: Narrative type
            best_score: Best match score
            participant_narratives: P0-4 - Narratives where user is a PARTICIPANT

        Returns:
            NarrativeSelectionResult
        """
        # 1. Prepare search result candidates (narrative metadata only)
        all_scores = {r.narrative_id: r.similarity_score for r in search_results}
        search_candidates = []

        for result in search_results:
            narrative = await self._crud.load_by_id(result.narrative_id)
            if narrative:
                candidate_name, candidate_desc = _candidate_labels(narrative)

                # The BM25 evidence, carried through from rank_pool. Without it
                # the judge sees only `Similarity score: 0.91` — a number that
                # can be 100% request-frame characters, on the very turns the
                # gate handed over BECAUSE the candidates were crowded.
                #
                # `raw_score` rides along un-rendered, as the marker for "this
                # candidate came from BM25 and therefore OWES evidence": step
                # 1.5 merges participant narratives into `search_results` at a
                # synthetic 0.5 similarity, so this list is not purely
                # BM25-sourced and the missing-evidence alarm downstream would
                # otherwise cry wolf on every participant turn.
                search_candidates.append({
                    "id": narrative.id,
                    "type": "search",
                    "name": candidate_name,
                    "description": candidate_desc,
                    "score": result.similarity_score,
                    "raw_score": result.raw_score,
                    "matched_terms": result.matched_terms,
                    "matched_content": result.matched_snippet,
                })

        logger.debug(f"[NarrativeSelect] Prepared {len(search_candidates)} search candidates for LLM judge")

        # 2. Use Repository to get default Narrative candidates (lazy import to avoid circular dependency)
        from xyz_agent_context.repository import NarrativeRepository
        db_client = await get_db_client()
        repo = NarrativeRepository(db_client)
        # C-1: with buckets governed, the judge gets real threads only. The
        # eight category names move into the instructions as vocabulary (see
        # prompts.NARRATIVE_UNIFIED_MATCH_INSTRUCTIONS) — a menu of eight fixed
        # entries WITH worked examples against at most three dynamic ones was a
        # menu that answered itself (measured: 60% of judge verdicts picked a
        # bucket, and 63 of those 93 had a real candidate available).
        default_narratives = (
            await repo.get_default_narratives(agent_id, user_id)
            if config.NARRATIVE_DEFAULT_BUCKETS_ENABLED
            else []
        )

        default_candidates = []
        for narrative in default_narratives:
            # Get examples from configuration
            config_item = next(
                (c for c in DEFAULT_NARRATIVES_CONFIG if c["name"] == narrative.narrative_info.name),
                None
            )

            default_candidates.append({
                "id": narrative.id,
                "type": "default",
                "name": narrative.narrative_info.name,
                "description": narrative.narrative_info.description,
                "examples": config_item["examples"] if config_item else []
            })

        # 2.5 (P0-4): Prepare PARTICIPANT Narrative candidates
        participant_candidates = []
        if participant_narratives:
            for narrative in participant_narratives:
                # Same labeller as the search branch above — no matched_terms /
                # matched_content: these never went through BM25 (they enter at
                # a synthetic neutral score), and inventing evidence for them
                # would be worse than showing none.
                candidate_name, candidate_desc = _candidate_labels(narrative)
                participant_candidates.append({
                    "id": narrative.id,
                    "type": "participant",  # P0-4: Changed to "participant"
                    "name": candidate_name,
                    "description": candidate_desc,
                })
            logger.info(f"P0-4: Added {len(participant_candidates)} PARTICIPANT candidates to LLM judgment")

        # 3. Call LLM for unified judgment
        llm_result = await self._llm_judge_unified(
            query=query,
            search_candidates=search_candidates,
            default_candidates=default_candidates,
            participant_candidates=participant_candidates  # P0-4: Pass PARTICIPANT candidates
        )

        # E1: the judge's verdict AND its reasoning. This is the only semantic
        # check in the pipeline, and its reasoning previously survived only
        # inside an f-string that went to loguru.
        if audit is not None:
            audit.judge_ran = True
            audit.judge_category = llm_result.get("matched_type") or "none"
            audit.judge_matched_id = llm_result.get("matched_id")
            audit.judge_reason = llm_result.get("reason") or ""

        # 4. Return based on LLM judgment result
        if llm_result["matched_id"]:
            matched_type = llm_result["matched_type"]
            matched_id = llm_result["matched_id"]
            reason = llm_result["reason"]

            if matched_type == "default":
                # Matched a default Narrative, return only this 1
                logger.info(f"LLM matched default Narrative: {matched_id}")
                matched_narrative = await self._crud.load_by_id(matched_id)

                return NarrativeSelectionResult(
                    narratives=[matched_narrative] if matched_narrative else [],
                    selection_reason=f"LLM matched default Narrative: {reason}",
                    selection_method="default_narrative_matched",
                    is_new=False,
                    best_score=best_score,
                    scores=all_scores,
                    retrieval_method=retrieval_method,
                    # evermemos_memories removed — EverMemOS decoupled from narrative selection
                )

            elif matched_type == "participant":
                # P0-4: Matched a PARTICIPANT Narrative (task priority)
                logger.info(f"LLM matched PARTICIPANT Narrative: {matched_id}")
                matched_narrative = await self._crud.load_by_id(matched_id)

                return NarrativeSelectionResult(
                    narratives=[matched_narrative] if matched_narrative else [],
                    selection_reason=f"LLM matched PARTICIPANT Narrative: {reason}",
                    selection_method="participant_narrative_matched",
                    is_new=False,
                    best_score=best_score,
                    scores=all_scores,
                    retrieval_method=retrieval_method,
                    # evermemos_memories removed — EverMemOS decoupled from narrative selection
                )

            elif matched_type == "search":
                # Matched a search result, return Top-K list
                logger.info(f"LLM matched search result: {matched_id}")
                narratives = []
                matched_narrative = await self._crud.load_by_id(matched_id)
                if matched_narrative:
                    narratives.append(matched_narrative)

                # Add other candidates (excluding already matched)
                for result in search_results[:top_k]:
                    if result.narrative_id != matched_id:
                        narrative = await self._crud.load_by_id(result.narrative_id)
                        if narrative and len(narratives) < top_k:
                            narratives.append(narrative)

                return NarrativeSelectionResult(
                    narratives=narratives,
                    selection_reason=f"LLM matched search result: {reason}",
                    selection_method="llm_confirmed",
                    is_new=False,
                    best_score=best_score,
                    scores=all_scores,
                    retrieval_method=retrieval_method,
                    # evermemos_memories removed — EverMemOS decoupled from narrative selection
                )

        # 4.5 (C-1) "No durable topic" — a verdict about the TURN, not a
        # destination. The retrieval tier deliberately stops here with an empty
        # list instead of creating: where such a turn lands depends on the
        # session anchor and on whether the surface persists history, and
        # neither is knowable from inside retrieval. NarrativeService.select
        # owns that decision (anchor-first). Creating here is exactly the
        # fragmentation this batch exists to avoid — a "你好" must not open a
        # thread while the user's real work thread is one lookup away.
        if llm_result.get("matched_type") == "no_topic":
            logger.info("LLM: no durable topic this turn — deferring the landing")
            return NarrativeSelectionResult(
                narratives=[],
                selection_reason=f"No durable topic: {llm_result.get('reason', '')}",
                selection_method="no_topic",
                is_new=False,
                no_durable_topic=True,
                best_score=best_score,
                scores=all_scores,
                retrieval_method=retrieval_method,
            )

        # 5. No match, create new Narrative
        logger.info("LLM determined no match with any Narrative, creating new topic")
        new_narrative = await self.create_from_query(
            query=query,
            user_id=user_id,
            agent_id=agent_id,
            narrative_type=narrative_type
        )

        return NarrativeSelectionResult(
            narratives=[new_narrative],
            selection_reason=f"LLM determined new topic: {llm_result.get('reason', 'No match')}",
            selection_method="new_created",
            is_new=True,
            best_score=best_score,
            scores=all_scores,
            retrieval_method=retrieval_method,
        )

    async def _llm_judge_unified(
        self,
        query: str,
        search_candidates: List[dict],
        default_candidates: List[dict],
        participant_candidates: Optional[List[dict]] = None,
    ) -> dict:
        """LLM unified judgment — delegates to _retrieval_llm module"""
        return await llm_judge_unified(
            query=query,
            search_candidates=search_candidates,
            default_candidates=default_candidates,
            participant_candidates=participant_candidates,
        )

    async def _get_participant_narratives(
        self,
        user_id: str,
        agent_id: str
    ) -> List[Narrative]:
        """
        Query Narratives where the user is a PARTICIPANT (2026-01-21 P0-4)

        Core logic:
        - Directly query Narratives whose actors contain {id: user_id, type: PARTICIPANT}
        - More direct and efficient than the previous Entity -> Job -> Narrative path

        Use cases:
        - Any scenario where non-Creator users need access to specific Narratives
        - Specific meaning (e.g., sales target, collaborator) is defined by the Agent's Awareness

        Args:
            user_id: User ID
            agent_id: Agent ID

        Returns:
            List of Narratives (all Narratives where the user is a PARTICIPANT)
        """
        import asyncio

        try:
            from xyz_agent_context.repository import NarrativeRepository

            db_client = await get_db_client()
            repo = NarrativeRepository(db_client)

            # Use Repository to query Narratives where user is a PARTICIPANT
            narratives = await repo.get_narratives_by_participant(
                user_id=user_id,
                agent_id=agent_id
            )

            if narratives:
                logger.info(f"PARTICIPANT Narratives: User {user_id} is a PARTICIPANT in {len(narratives)} Narratives")
            else:
                logger.debug(f"PARTICIPANT Narratives: User {user_id} has no PARTICIPANT Narratives")

            return narratives

        except Exception as e:
            logger.exception(f"PARTICIPANT Narratives: Query failed: {e}")
            return []

    async def create_from_query(
        self,
        query: str,
        user_id: str,
        agent_id: str,
        narrative_type: NarrativeType
    ) -> Narrative:
        """Create a new Narrative from the query (BM25 routing surface only).

        NAME and DESCRIPTION are built from the query with the channel routing
        prefix removed. Naming a thread "[From <sender>] ..." turns it into a
        magnet for every later message from that channel: the sender tokens
        then sit in the thread's OWN retrieval surface at a low in-pool df, so
        the next message matches its own line and skips the judge (prod audit
        1492 reached margin 357.79 exactly this way; four such lines exist in
        prod today). `topic_keywords` is deliberately left on the raw query —
        that field has an undecided two-writer design (A-kw) and is read-only
        for this change.
        """
        # Extract keywords (the BM25 routing surface)
        topic_keywords = extract_keywords(query)

        # The naming surface, without the channel label. Falls back to the raw
        # query when a message is nothing BUT a prefix — an unnamed thread is
        # worse than a slightly noisy name.
        naming_text = strip_routing_prefix(query).strip() or query

        # Generate topic hint
        topic_hint = truncate_text(naming_text, config.SUMMARY_MAX_LENGTH)

        # Generate title
        title = truncate_text(naming_text, 30)

        # And the description. Its two siblings above were truncated and it was
        # not, which is how prod ended up with a 198,398-character description
        # welded into a BM25 index that the updater never rewrites. Bounded here
        # AND clamped again in `_crud.create` — the call site states the intent,
        # the funnel covers the other two writers (the LLM's create_narrative
        # signal and the HTTP route).
        description = truncate_text(
            f"Created based on query: {naming_text}", config.DESCRIPTION_MAX_LENGTH
        )

        # Create Narrative
        narrative = await self._crud.create(
            agent_id=agent_id,
            user_id=user_id,
            narrative_type=narrative_type,
            title=title,
            description=description
        )

        # BM25 routing surface. `searchable_text()` is the one definition:
        # name + current_summary + (description, only while unsummarised) +
        # topic_keywords. The old comment here listed three fields and omitted
        # description, which is how it stayed a tombstone in the index for two
        # months without anyone noticing. Embedding
        # fields (routing_embedding / embedding_updated_at / VectorStore /
        # embeddings_store) are retired — narrative routing is vector-free.
        narrative.topic_keywords = topic_keywords
        narrative.topic_hint = topic_hint

        await self._crud.save(narrative)

        logger.info(f"Created new Narrative: {narrative.id}")
        return narrative
