"""
@file_name: narrative_service.py
@author: NetMind.AI
@date: 2025-12-22
@description: Narrative service protocol layer

This is the public interface for NarrativeService; all concrete implementations are delegated to the _narrative_impl module.

Features:
1. select() - Select/create Narrative
2. update_with_event() - Update Narrative with an Event
3. CRUD operations
4. Instance management
5. Prompt generation
"""

from __future__ import annotations

import time as _perf
from typing import List, Optional, Tuple, TYPE_CHECKING

from dataclasses import dataclass

from loguru import logger

from .models import (
    ConversationSession,
    Event,
    Narrative,
    NarrativeActor,
    NarrativeSelectionResult,
    NarrativeType,
    RoutingAudit,
)
from ._narrative_impl import (
    NarrativeCRUD,
    NarrativeRetrieval as _NarrativeRetrieval,
    NarrativeUpdater as _NarrativeUpdater,
    InstanceHandler,
    PromptBuilder,
    ContinuityDetector,
)

if TYPE_CHECKING:
    from xyz_agent_context.utils.db.database import AsyncDatabaseClient
    from xyz_agent_context.schema.module_schema import InstanceStatus


def resolve_retrieval_text(retrieval_anchor: Optional[str], input_content: str) -> str:
    """Pick the query text for narrative retrieval (BM25) / continuity.

    A trigger that knows how (chat / IM channel / message bus) passes a clean
    ``retrieval_anchor`` = "[From <name>] <this-turn body>". When present we
    match on that, so BM25 keys off the clean this-turn body instead of the
    noisy full execution prompt. When absent/blank we fall back to the raw
    ``input_content``. See the 2026-06-01 design doc.
    """
    if retrieval_anchor and retrieval_anchor.strip():
        return retrieval_anchor
    return input_content


def is_reusable_anchor(narrative) -> bool:
    """Is this anchored narrative a thread a turn may simply stay on?

    THE one definition, consumed by all three anchor-reuse decision points —
    the continuity guard in ``select()``, the no-topic landing in
    ``_land_no_topic_turn``, and ``step_1_fast_select``'s session reuse. The
    independent review (2026-08-21, Important #3) caught the fast path missing
    the check the slow path had: sessions still anchored to a legacy default
    bucket (26.4% of prod user turns at C-1 ship time) were re-pinned to the
    bucket every fast turn while the slow path pushed them out — two paths
    fighting over the same invariant, because it lived as two literals.

    A default bucket stops being a reusable thread when C-1 governance is on;
    with the rollback flag flipped, buckets are containers again and reuse is
    the old, intended behaviour.
    """
    from .config import config

    if narrative is None:
        return False
    return not (
        narrative.is_special == "default"
        and not config.NARRATIVE_DEFAULT_BUCKETS_ENABLED
    )


def _minutes_since(session: Optional[ConversationSession]) -> Optional[float]:
    """Minutes since the previous turn, or None when there was none.

    Naive timestamps are read as UTC — the same guard the continuity tier
    applies, for the same reason: a naive `last_query_time` from an older row
    would otherwise make the subtraction raise, and the merged call would fail
    into its fallback for a formatting reason.
    """
    if session is None or session.last_query_time is None:
        return None
    from datetime import datetime, timezone

    last = session.last_query_time
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - last).total_seconds() / 60.0


@dataclass(frozen=True)
class FastSelectResult:
    """Outcome of one fast-path BM25 probe (``select_fast``).

    In-process value object (never crosses a wire, hence a dataclass and
    not a pydantic model). ``narrative`` is the decisive pick under the
    active floor (strong override floor when probing against a live
    anchor, noise floor otherwise); ``top1_raw`` rides into the audit row
    so the floors can be calibrated from data.
    """

    narrative: Optional[Narrative] = None
    top1_raw: Optional[float] = None


class NarrativeService:
    """
    Narrative Unified Service - Main interface for AgentRuntime

    This is a protocol layer; all concrete implementations are delegated to the _narrative_impl module.

    Main features:
    1. select() - Select the appropriate Narrative
    2. update_with_event() - Update Narrative with an Event
    3. CRUD operations
    4. Instance management
    5. Prompt generation

    Usage:
        >>> service = NarrativeService(agent_id="agent_1")
        >>> result = await service.select(agent_id, user_id, input_content)
        >>> await service.update_with_event(narrative, event)
    """

    def __init__(
        self,
        agent_id: str,
        database_client: Optional["AsyncDatabaseClient"] = None
    ):
        """
        Initialize Narrative Service

        Args:
            agent_id: Agent ID
            database_client: Database client (optional)
        """
        self.agent_id = agent_id
        self._database_client = database_client

        # Implementation modules
        self._crud = NarrativeCRUD(agent_id)
        self._retrieval = _NarrativeRetrieval(agent_id)
        self._updater = _NarrativeUpdater(agent_id)
        self._instance_handler = InstanceHandler(agent_id)

        # Session and Continuity (lazy loaded)
        self._session_service = None
        self._continuity_detector = None

        logger.info(f"NarrativeService initialized (agent_id={agent_id})")

    # =========================================================================
    # Dependency Injection
    # =========================================================================

    def set_event_service(self, event_service):
        """Inject EventService"""
        self._retrieval.set_event_service(event_service)
        self._updater.set_event_service(event_service)

    @property
    def database_client(self) -> Optional["AsyncDatabaseClient"]:
        """Get the database client"""
        return self._database_client

    # =========================================================================
    # Main Feature: select()
    # =========================================================================

    async def select_fast(
        self,
        agent_id: str,
        user_id: str,
        query: str,
        *,
        against_live_anchor: bool = False,
    ) -> "FastSelectResult":
        """BM25 top-1 probe — the fast-mode (F28) narrative path.

        Zero LLM, zero creation, zero session writes: one keyword search
        (top_k=1) plus, on a decisive pick, a CRUD load. What the caller
        does with the outcome is the surface's call: voice runs a miss
        bare; durable chat surfaces reuse the session anchor or fall
        through to ``create_fast`` below. The continuity / LLM tiers
        remain exclusive to the full select().

        ``against_live_anchor``: the caller holds a live session anchor,
        so a decisive pick here would STEAL the turn away from the active
        thread — the pick requires the strong FAST_ANCHOR_OVERRIDE_FLOOR
        instead of the noise-filter RAW_FLOOR. The result also carries
        ``top1_raw`` so the audit row records the score that justified —
        or failed to justify — the pick (thresholds in config.py).
        """
        from .config import config

        floor = (
            config.FAST_ANCHOR_OVERRIDE_FLOOR
            if against_live_anchor
            else config.NARRATIVE_MATCH_RAW_FLOOR
        )
        results = await self._retrieval.keyword_search(
            query=query, user_id=user_id, agent_id=agent_id, top_k=1
        )
        top1_raw = results[0].raw_score if results else None
        narrative: Optional[Narrative] = None
        if top1_raw is not None and top1_raw >= floor:
            narrative = await self._crud.load_by_id(results[0].narrative_id)
        return FastSelectResult(narrative=narrative, top1_raw=top1_raw)

    async def audit_fast(
        self,
        agent_id: str,
        user_id: str,
        query: str,
        *,
        retrieval_method: str,
        chosen_narrative_id: Optional[str],
        trigger: str = "",
        is_user_chat: bool = True,
        keyword_ms: Optional[int] = None,
        is_new: bool = False,
        top1_raw: Optional[float] = None,
    ) -> None:
        """Best-effort audit row for one fast-path routing decision.

        The fast path hits, reuses or creates — all decisions with
        persistent consequences — so it must leave the same DB evidence
        the full select() does (loguru rotates away; the audit table is
        the reliable record). Continuity/judge fields stay at their
        "tier did not run" defaults (None, not zero) so latency and
        routing stats never mix "skipped" with "ran and found nothing".
        Delegates to ``_write_audit`` — best-effort, never breaks a turn.
        """
        audit = RoutingAudit(
            agent_id=agent_id,
            user_id=user_id,
            query_text=query,
            trigger=trigger,
            is_user_chat=is_user_chat,
            keyword_ms=keyword_ms,
            # The BM25 score that justified (or failed to justify) the
            # pick — the calibration data for FAST_ANCHOR_OVERRIDE_FLOOR.
            gate_top1_raw=top1_raw,
            selection_method="fast",
            retrieval_method=retrieval_method,
            chosen_narrative_id=chosen_narrative_id,
            is_new=is_new,
        )
        await self._write_audit(audit, {})

    async def create_fast(
        self, agent_id: str, user_id: str, query: str
    ) -> Narrative:
        """CRUD-only narrative creation for the fast path (no LLM tier).

        Delegates to the retrieval impl's query-based creator so the new
        narrative carries the same BM25 routing surface (title, keywords,
        topic hint) as one created by the full select() flow — the next
        turn's retrieval sees no difference in how it was born.
        """
        return await self._retrieval.create_from_query(
            query=query,
            user_id=user_id,
            agent_id=agent_id,
            narrative_type=NarrativeType.CHAT,
        )

    async def select(
        self,
        agent_id: str,
        user_id: str,
        input_content: str,
        max_narratives: Optional[int] = None,
        session: Optional[ConversationSession] = None,
        awareness: Optional[str] = None,
        is_user_chat: bool = True,
        retrieval_anchor: Optional[str] = None,
        trigger: str = "",
    ) -> NarrativeSelectionResult:
        """
        Select the appropriate Narratives

        Workflow:
        1. Detect topic continuity
        2. BM25 keyword match or create new Narrative
        3. Return results

        Args:
            agent_id: Agent ID
            user_id: User ID
            input_content: User input
            max_narratives: Maximum return count
            session: Session object
            awareness: Agent self-awareness content (optional)
            is_user_chat: True iff the current run was triggered by a real
                user chat message. Background triggers (cron jobs, message_bus
                pings, IM webhooks, callbacks) pass False so the Session's
                `last_query` / `last_response` / `current_narrative_id` —
                which feed continuity detection on the *next* user message —
                stay anchored to the last real user exchange and don't get
                overwritten by intervening machine traffic.

        Returns:
            NarrativeSelectionResult: Contains Narrative list, selection reason, and other complete info
        """
        from .config import config
        from xyz_agent_context.utils.logging import timed

        max_narratives = max_narratives or config.MAX_NARRATIVES_IN_CONTEXT
        logger.info("NarrativeService.select() started")

        # Match against the clean anchor (sender + this-turn body) when a
        # trigger provided one; else the raw input_content. See 2026-06-01 design.
        query_text = resolve_retrieval_text(retrieval_anchor, input_content)

        # Merged routing (off by default): BM25 runs FIRST, then either a
        # zero-LLM shutter or ONE call answers both questions the rest of this
        # method asks serially. An early return rather than a branch woven
        # through the body — everything below it is the two-call path exactly as
        # it was, which is what makes "flag off = today's behaviour" a property
        # you can read instead of a claim you have to trust.
        if config.NARRATIVE_MERGED_ROUTING_ENABLED:
            return await self._select_merged(
                agent_id=agent_id,
                user_id=user_id,
                query_text=query_text,
                max_narratives=max_narratives,
                session=session,
                awareness=awareness,
                is_user_chat=is_user_chat,
                trigger=trigger,
            )

        # Continuity detection — wrapped in timed() so its LLM call is visible
        # as a discrete slice of step.1 instead of getting lumped into
        # the "everything else" bucket.
        is_continuous = False
        continuity_reason = ""
        continuity_ran = False
        continuity_confidence: Optional[float] = None
        # None until the tier actually runs. See RoutingAudit.continuity_ms for
        # why this must never default to 0.
        _continuity_ms: Optional[int] = None
        _retrieve_ms: Optional[int] = None
        # Run continuity against the last *user-visible* exchange — that is
        # either the user's previous query OR the agent's last reply the user
        # is now responding to (a proactive job/heartbeat message anchors only
        # last_response, with last_query empty). Was `if session.last_query`,
        # which skipped continuity entirely for proactive-message replies.
        if session and (session.last_query or session.last_response):
            try:
                detector = self._get_continuity_detector()
                if detector:
                    # Get the current Narrative (if any)
                    current_narrative = None
                    if session.current_narrative_id:
                        current_narrative = await self._crud.load_by_id(session.current_narrative_id)

                    # C-1 slice 5: a default bucket is a VERDICT about some
                    # earlier turn, not a thread — there is nothing to
                    # continue. Left to itself the continuity tier held 59 of
                    # the replay's 155 bucket-resident turns there, in chains
                    # up to 11 turns long, and every one of those turns is
                    # unrecallable (a bucket's retrieval surface never
                    # updates). Skip the tier outright rather than ask and
                    # ignore: the answer costs a full helper round trip.
                    if (
                        current_narrative is not None
                        and not is_reusable_anchor(current_narrative)
                    ):
                        logger.info(
                            "[NarrativeSelect] anchor is a default bucket — "
                            "routing this turn instead of continuing it"
                        )
                        detector = None

                if detector:
                    _t_continuity = _perf.monotonic()
                    with timed("narrative.continuity_detect") as t:
                        result = await detector.detect(
                            current_query=query_text,
                            session=session,
                            current_narrative=current_narrative,
                            awareness=awareness
                        )
                        # Tag the timer with the model the helper LLM
                        # actually ended up using inside detector.detect
                        # (resolution happens deep in OpenAIAgentsSDK —
                        # we read it back via the contextvar set there).
                        from xyz_agent_context.agent_framework.adapters.openai_agents import (
                            get_last_llm_call_info,
                        )
                        info = get_last_llm_call_info()
                        if info:
                            t.tag(**info)
                    _continuity_ms = int((_perf.monotonic() - _t_continuity) * 1000)
                    logger.debug(f"Continuity detection reason: {result.reason}")
                    is_continuous = result.is_continuous
                    continuity_reason = result.reason
                    continuity_ran = True
                    continuity_confidence = result.confidence
            except Exception as e:
                logger.warning(f"Continuity detection failed: {e}")

        narratives: List[Narrative] = []
        selection_reason = ""
        selection_method = ""
        retrieval_method = ""  # Retrieval method identifier

        if is_continuous and session and session.current_narrative_id:
            # Continuity detection is True: main Narrative is the current one, but still need to retrieve Top-K Narratives
            # This allows including conversation history from other related Narratives
            main_narrative = await self._crud.load_by_id(session.current_narrative_id)
            if main_narrative:
                logger.info(f"Continuity detection passed, main Narrative: {main_narrative.id}")
                selection_reason = f"Topic continuity detection passed: {continuity_reason}"
                selection_method = "continuous"
                retrieval_method = "session"  # Continuity: active thread from session, no keyword search needed

                # The main Narrative is the active conversation thread. Vector
                # retrieval of surrounding related narratives is retired
                # (embeddings gone); the non-continuous branch below uses BM25.
                narratives = [main_narrative]

                logger.info(f"Continuity detection: returning main Narrative {main_narrative.id}")

        audit: Optional[RoutingAudit] = None
        audit_snapshots: dict = {}
        no_durable_topic = False

        if not narratives:
            # Not continuous or continuity detection failed: retrieve Top-K
            _t_retrieve = _perf.monotonic()
            with timed("narrative.retrieve_top_k"):
                retrieval_result = await self._retrieval.retrieve_top_k(
                    query=query_text,
                    user_id=user_id,
                    agent_id=agent_id,
                    top_k=max_narratives,
                    # The bypass rule needs to know what thread we are already
                    # in: skipping the judge is only allowed for a turn that
                    # STAYS there. Reading the anchor here rather than inside
                    # the retrieval tier keeps Session ownership in one place —
                    # the tier below has no business knowing what a Session is.
                    anchor_narrative_id=(
                        session.current_narrative_id if session else None
                    ),
                    # Background triggers deliberately never advance that
                    # anchor (see the Session update block at the end of this
                    # method), so they have none to match and the anchor rule
                    # does not apply to them.
                    is_user_chat=is_user_chat,
                )
            _retrieve_ms = int((_perf.monotonic() - _t_retrieve) * 1000)
            narratives = retrieval_result.narratives
            selection_reason = retrieval_result.selection_reason
            selection_method = retrieval_result.selection_method
            retrieval_method = retrieval_result.retrieval_method
            audit = retrieval_result.audit
            audit_snapshots = retrieval_result.audit_snapshots
            no_durable_topic = retrieval_result.no_durable_topic

            if no_durable_topic:
                narratives, selection_method, selection_reason, is_new = (
                    await self._land_no_topic_turn(
                        agent_id=agent_id,
                        user_id=user_id,
                        query_text=query_text,
                        session=session,
                        reason=selection_reason,
                    )
                )
                if audit is not None:
                    audit.selection_method = selection_method
                    audit.chosen_narrative_id = (
                        narratives[0].id if narratives else None
                    )
                    audit.is_new = is_new
            if audit is not None:
                audit.retrieve_ms = _retrieve_ms
        else:
            # Continuity short-circuited the retrieval tier, so there is no
            # pool and no gate — but this is the path that most needs a trail:
            # a false "continuous" re-uses session.current_narrative_id with no
            # topic check, and since each turn writes that id straight back,
            # one bad verdict can hold the thread for several turns. Nothing
            # recorded it before, which is why its real rate is unknown.
            audit = RoutingAudit(
                agent_id=agent_id, user_id=user_id, query_text=query_text,
                selection_method=selection_method,
                retrieval_method=retrieval_method,
                chosen_narrative_id=narratives[0].id if narratives else None,
            )
            # Slice 0 — record the pool this turn never consulted.
            #
            # The verdict above is already final: `narratives`,
            # `selection_method` and `chosen_narrative_id` are set and nothing
            # below may touch them. What the recorder adds is the one thing the
            # merged-routing design cannot get anywhere else — the shutter's
            # releasable population on continuity turns, currently bounded only
            # at 6%-39% because these rows carry no pool to reconstruct from.
            #
            # Awaited on purpose (~13.5ms: two DB reads + one snapshot-dedup
            # SELECT; the real line item is the shadow row's full-pool
            # candidates_json, 10KB-scale): a `create_task` here
            # would race the audit write below and turn any failure into a GC
            # warning nobody reads (incident lesson #2).
            #
            # The guard is scoped to the instrument and nothing else. Losing a
            # measurement is cheaper than failing a user's turn — the same rule
            # the audit repository already states — but a failure in the
            # DECISION path above still propagates, because it is outside this
            # block.
            # User-chat only: background triggers (job / message_bus / IM
            # webhook) have no session anchor by design, so their shadow rows
            # answer nothing about the shutter's releasable population
            # (bypass_reason would be background_scope on every one) while
            # still paying the recording cost — ~30% of dev turns are
            # message_bus. Deliberate scope, not an omission.
            if config.NARRATIVE_SHADOW_POOL_RECORD and is_user_chat:
                await self._record_shadow_pool(
                    query_text=query_text, user_id=user_id, agent_id=agent_id,
                    session=session, is_user_chat=is_user_chat,
                    top_k=max_narratives,
                    audit=audit, snapshots=audit_snapshots,
                )

        self._advance_session_anchor(session, query_text, narratives, is_user_chat)

        logger.info(f"[NarrativeSelect] completed: {len(narratives)} Narratives, method={selection_method}")

        if audit is not None:
            audit.trigger = trigger
            audit.is_user_chat = is_user_chat
            audit.continuity_ran = continuity_ran
            audit.continuity_is_continuous = is_continuous if continuity_ran else None
            audit.continuity_confidence = continuity_confidence
            audit.continuity_reason = continuity_reason
            # Stays None when continuity never ran (no session anchor to judge
            # against) — a 0 there would read as "the tier is free", which is
            # the opposite of true: it is a full helper-LLM round trip.
            audit.continuity_ms = _continuity_ms
            await self._write_audit(audit, audit_snapshots)

        return NarrativeSelectionResult(
            narratives=narratives,
            selection_reason=selection_reason,
            selection_method=selection_method,
            no_durable_topic=no_durable_topic,
            is_new=(selection_method == "new_created"),
            best_score=None,
            retrieval_method=retrieval_method,
        )

    @staticmethod
    def _advance_session_anchor(
        session: Optional[ConversationSession],
        query_text: str,
        narratives: List[Narrative],
        is_user_chat: bool,
    ) -> None:
        """Move the continuity anchor to the thread this turn landed on.

        Only user-initiated runs (chat) write `last_query` /
        `current_narrative_id` — background trigger runs (job / message_bus /
        lark / callback) must leave them untouched so the NEXT user message gets
        its continuity judged against the previous user exchange rather than
        against whatever cron job or bus ping ran in between.

        Extracted because both routing paths must obey it identically. The
        anchor rule living as two literals is exactly how the fast path and the
        slow path ended up fighting over the same invariant (independent review,
        2026-08-21, Important #3), and a second decider is a second chance at
        that.
        """
        if not (session and narratives and is_user_chat):
            return
        from datetime import datetime, timezone

        session.last_query = query_text
        session.current_narrative_id = narratives[0].id
        session.query_count += 1
        session.last_query_time = datetime.now(timezone.utc)

    async def _select_merged(
        self,
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
        landing), `create_from_query`, `_land_no_topic_turn`. Downstream —
        step_1, step_4, the ChatModule — reads `narratives` / `is_new` /
        `no_durable_topic` / `retrieval_method` and never branches on
        `selection_method`, so nothing outside this file learns there was a
        change.

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
        from .config import config
        from xyz_agent_context.utils.logging import timed
        from ._narrative_impl.merged_router import (
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
            anchor = await self._crud.load_by_id(anchor_id)
        # THE one definition, shared with the fast path and the no-topic
        # landing: a legacy default bucket is a verdict about an earlier turn,
        # not a thread anyone may continue.
        continuable = is_reusable_anchor(anchor)

        with timed("narrative.merged.prepare"):
            prep = await self._retrieval.prepare_merged_routing(
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

        narratives: List[Narrative] = []
        selection_method = ""
        selection_reason = ""
        retrieval_method = ""
        is_new = False
        no_durable_topic = False

        # `anchor_match` is the only verdict that opens the shutter, and it is
        # unreachable without the anchor id passed above — so the `anchor is not
        # None` half is belt-and-braces, and it is written as a CONDITION rather
        # than a comment because the alternative (asserting it in prose and
        # indexing anyway) is how a "cannot happen" becomes a None in a list.
        if prep.shutter_granted and anchor is not None:
            narratives = [anchor]
            selection_method = "anchor_confirmed"
            selection_reason = f"Confirmed the anchored thread: {prep.bypass.detail}"
            retrieval_method = "session"
            logger.info(f"[NarrativeSelect] shutter — {prep.bypass.detail}")
        else:
            excluded: set = set(
                n.id for n in prep.participant_narratives
            )
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
                minutes_since_previous=_minutes_since(session),
                menu=await self._retrieval.build_menu_candidates(menu_results),
                participants=self._retrieval.build_participant_candidates(
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

            if not decision.ok:
                # RULE 6 — a failure is not a verdict. Two production incidents
                # (D19) had this exact shape: the deciding tier failed, the
                # failure fell through to creation, the created thread became
                # the anchor, and the updater rewrote it until the lexical
                # evidence agreed. So: stay where we already were, flagged; and
                # where there is nowhere to stay, create — but never silently,
                # never as a switch.
                if anchor is not None and continuable:
                    narratives = [anchor]
                    selection_method = "merged_fallback_anchor"
                    selection_reason = (
                        f"Merged routing unavailable, held the anchored thread: "
                        f"{decision.reason}"
                    )
                    retrieval_method = "session"
                else:
                    created = await self._retrieval.create_from_query(
                        query=query_text, user_id=user_id, agent_id=agent_id,
                        narrative_type=NarrativeType.CHAT,
                    )
                    narratives = [created]
                    selection_method = "merged_fallback_new"
                    selection_reason = (
                        f"Merged routing unavailable and no thread to hold the "
                        f"turn: {decision.reason}"
                    )
                    retrieval_method = "keyword"
                    is_new = True

            elif decision.verdict == VERDICT_CONTINUE_ANCHOR:
                narratives = [anchor] if anchor else []
                selection_method = "merged_continue"
                selection_reason = f"Continued the anchored thread: {decision.reason}"
                retrieval_method = "session"

            elif decision.verdict in (VERDICT_MATCH, VERDICT_PARTICIPANT):
                chosen_id = resolve_choice(decision, routing_input)
                if decision.verdict == VERDICT_MATCH:
                    # The judge's own landing, called rather than copied. The
                    # trailing rows are context for the agent prompt and come
                    # from the menu: the anchored thread the model just left is
                    # deliberately not re-appended as context for leaving it.
                    narratives = await self._retrieval.assemble_match_landing(
                        chosen_id or "", menu_results, max_narratives
                    )
                    selection_method = "merged_match"
                    selection_reason = f"Switched to an existing thread: {decision.reason}"
                else:
                    matched = (
                        await self._crud.load_by_id(chosen_id) if chosen_id else None
                    )
                    narratives = [matched] if matched else []
                    selection_method = "merged_participant"
                    selection_reason = (
                        f"Matched a thread the user participates in: {decision.reason}"
                    )
                retrieval_method = "keyword"

            elif decision.verdict == VERDICT_NEW:
                created = await self._retrieval.create_from_query(
                    query=query_text, user_id=user_id, agent_id=agent_id,
                    narrative_type=NarrativeType.CHAT,
                )
                narratives = [created]
                selection_method = "merged_new"
                selection_reason = f"A new subject: {decision.reason}"
                retrieval_method = "keyword"
                is_new = True

            elif decision.verdict == VERDICT_NO_TOPIC:
                # The verdict carries no destination; `_land_no_topic_turn` owns
                # that, anchor-first, and its freeze semantics are untouched — a
                # greeting must never rename the work it interrupted.
                no_durable_topic = True
                retrieval_method = "keyword"
                narratives, selection_method, selection_reason, is_new = (
                    await self._land_no_topic_turn(
                        agent_id=agent_id, user_id=user_id, query_text=query_text,
                        session=session, reason=decision.reason,
                    )
                )

        self._advance_session_anchor(session, query_text, narratives, is_user_chat)

        logger.info(
            f"[NarrativeSelect] merged: {len(narratives)} Narratives, "
            f"method={selection_method}"
        )

        audit.selection_method = selection_method
        audit.retrieval_method = retrieval_method
        audit.chosen_narrative_id = narratives[0].id if narratives else None
        audit.is_new = is_new
        # `continuity_ms` / `judge_ms` stay NULL: those tiers did not run, and a
        # 0 there would read as "the tier is free" — the opposite of true. The
        # merged call's own cost is `merged_ms`, which nests nothing.
        await self._write_audit(audit, snapshots)

        return NarrativeSelectionResult(
            narratives=narratives,
            selection_reason=selection_reason,
            selection_method=selection_method,
            no_durable_topic=no_durable_topic,
            is_new=is_new,
            best_score=None,
            retrieval_method=retrieval_method,
        )

    async def _land_no_topic_turn(
        self,
        *,
        agent_id: str,
        user_id: str,
        query_text: str,
        session: Optional[ConversationSession],
        reason: str,
    ) -> tuple:
        """Place a turn the judge called "no durable topic" (C-1, plan 4-A').

        The judge answered a question about the TURN ("is there anything here
        worth remembering as its own thread?"); it did not name a destination.
        This is where the destination is decided, and the rule is anchor-first
        — deliberately the same shape ``step_1_fast_select`` already settled on
        for the fast path, so the two paths cannot drift into disagreeing about
        where a contentless turn belongs:

        1. **A live anchor on a real thread → reuse it.** A "你好" in the middle
           of a task belongs to the task (annotation protocol R1), and the user
           expects the agent to still know what they were doing. The thread's
           retrieval surface is NOT touched — see ``no_durable_topic`` on
           NarrativeSelectionResult for why a greeting must never get to rename
           the work it interrupted.
        2. **No anchor → create.** Chat history endpoints are
           narrative-scoped and the ChatModule instance hangs off the narrative,
           so running bare here would make a first-contact turn vanish from the
           user's own history. The created thread is not junk: it becomes the
           anchor, and the updater renames it as the real subject emerges.

        A third branch (ephemeral surface → run bare, `no_topic_bare`) was
        REMOVED on review (2026-08-21, Important #2): every ephemeral
        TurnProfile also selects the bm25_top1 fast path, so this method never
        received anything but "durable" — the branch was unreachable in
        production and a test was pinning behaviour the system could not
        exhibit (the exact `matched_content` failure shape this batch
        cleaned up elsewhere). If an ephemeral profile ever takes the slow
        path, re-add it deliberately, wired from TurnProfile at the two
        `select()` call sites in step_1_select_narrative.

        Returns ``(narratives, selection_method, reason, is_new)``.
        """
        anchor_id = getattr(session, "current_narrative_id", None) if session else None
        if anchor_id:
            anchored = await self._crud.load_by_id(anchor_id)
            # A bucket anchor is not a thread to reuse (slice 5 already refused
            # to continue one); fall through and let the turn land properly.
            if is_reusable_anchor(anchored):
                logger.info(
                    f"[NarrativeSelect] no durable topic — reusing anchor "
                    f"{anchored.id} without touching its surface"
                )
                return (
                    [anchored],
                    "no_topic_anchored",
                    f"No durable topic; kept on the active thread: {reason}",
                    False,
                )

        created = await self._retrieval.create_from_query(
            query=query_text,
            user_id=user_id,
            agent_id=agent_id,
            narrative_type=NarrativeType.CHAT,
        )
        logger.info(
            f"[NarrativeSelect] no durable topic and no anchor — created "
            f"{created.id} so the turn stays in history"
        )
        return (
            [created],
            "new_created",
            f"No durable topic, no thread to keep it on: {reason}",
            True,
        )

    # =========================================================================
    # Update Features
    # =========================================================================

    async def update_with_event(
        self,
        narrative: Narrative,
        event: Event,
        is_main_narrative: bool = True,
        is_default_narrative: bool = False
    ) -> Narrative:
        """
        Update Narrative with an Event

        Args:
            narrative: Narrative object
            event: Event object
            is_main_narrative: Whether this is the main Narrative
                - True: Full update (LLM dynamic update)
                - False: Basic update only (associate Event, update dynamic_summary)
            is_default_narrative: Whether this is a default Narrative (is_special="default")
                - True: Only add event_id, no other updates
                - False: Normal update
        """
        return await self._updater.update_with_event(
            narrative,
            event,
            is_main_narrative=is_main_narrative,
            is_default_narrative=is_default_narrative
        )

    # =========================================================================
    # CRUD Operations
    # =========================================================================

    async def load_narrative_from_db(self, narrative_id: str) -> Optional[Narrative]:
        """Load a Narrative from the database"""
        return await self._crud.load_by_id(narrative_id)

    async def save_narrative_to_db(self, narrative: Narrative) -> int:
        """Save a Narrative to the database"""
        return await self._crud.save(narrative)

    async def load_narratives_by_agent_user(
        self,
        agent_id: str,
        user_id: str,
        limit: int = 10
    ) -> List[Narrative]:
        """Load Narratives by Agent and User"""
        return await self._crud.load_by_agent_user(agent_id, user_id, limit)

    async def create_narrative(
        self,
        agent_id: str,
        user_id: str,
        narrative_type: NarrativeType = NarrativeType.CHAT,
        title: str = "New Narrative",
        description: str = "",
        actors: Optional[List[NarrativeActor]] = None,
        save_to_db: bool = True,
    ) -> Narrative:
        """Create a new Narrative"""
        return await self._crud.create(
            agent_id=agent_id,
            user_id=user_id,
            narrative_type=narrative_type,
            title=title,
            description=description,
            actors=actors,
            save_to_db=save_to_db
        )

    # =========================================================================
    # Instance Management
    # =========================================================================

    async def handle_instance_completion(
        self,
        narrative_id: str,
        instance_id: str,
        new_status: "InstanceStatus",
        narrative: Optional[Narrative] = None,
        save_to_db: bool = True
    ) -> List[str]:
        """Handle Instance completion event"""
        return await self._instance_handler.handle_completion(
            narrative_id=narrative_id,
            instance_id=instance_id,
            new_status=new_status,
            narrative=narrative,
            save_to_db=save_to_db
        )

    # =========================================================================
    # Prompt Generation
    # =========================================================================

    async def combine_main_narrative_prompt(
        self,
        narrative: Narrative,
        include_volatile: bool = True,
    ) -> str:
        """Generate the main Prompt for a Narrative.

        include_volatile=False renders the byte-stable half only (R4
        turn-context relocation); the volatile fields then travel via
        combine_narrative_turn_prompt().
        """
        return await PromptBuilder.build_main_prompt(narrative, include_volatile=include_volatile)

    async def combine_narrative_turn_prompt(self, narrative: Narrative) -> str:
        """Generate the per-turn volatile Narrative block (R4 relocation)."""
        return await PromptBuilder.build_turn_prompt(narrative)

    # =========================================================================
    # Internal Methods
    # =========================================================================

    async def _write_audit(self, audit: RoutingAudit, snapshots: dict) -> None:
        """Persist one routing decision (E1). Best-effort — never breaks a turn.

        Written inline rather than in a detached task: it is two small queries
        against a connection this turn already holds, and a fire-and-forget
        `create_task` here would be a fresh instance of the very hazard that
        made narrative summaries fail silently for two weeks (an unawaited
        Task whose exception surfaces only as a GC warning — incident lesson
        #2). If this ever shows up in step.1 timings, coalesce the writes;
        do not detach them.
        """
        try:
            from xyz_agent_context.repository.narrative_routing_audit_repository import (
                NarrativeRoutingAuditRepository,
            )
            from xyz_agent_context.utils.db.db_factory import get_db_client

            db = self._database_client or await get_db_client()
            await NarrativeRoutingAuditRepository(db).record(audit, snapshots)
        except Exception as e:  # noqa: BLE001 — the observer must not break the observed
            logger.warning(f"[narrative.audit] not recorded: {type(e).__name__}: {e}")

    async def _record_shadow_pool(
        self,
        *,
        query_text: str,
        user_id: str,
        agent_id: str,
        session: ConversationSession,
        is_user_chat: bool,
        top_k: int,
        audit: RoutingAudit,
        snapshots: dict,
    ) -> None:
        """Fill a continuity turn's audit row with the pool it never consulted.

        A thin seam over ``NarrativeRetrieval.record_pool_only`` so the failure
        boundary is one named place rather than a bare try/except inline in
        ``select``. It exists to be monkeypatched off in the invariance test:
        that test runs the same turn with and without it and asserts the
        decided fields are identical, which is the property that makes this an
        instrument rather than a change.

        ``session`` is NOT Optional here. Reaching this branch requires
        ``narratives`` to be non-empty, and the only assignment to it is guarded
        by ``if is_continuous and session and session.current_narrative_id``, so
        both are known-true. The old ``if session else None`` was a branch that
        could never be taken, and the missing annotation is what hid that.
        (The similar-looking guard in ``_land_no_topic_turn`` is real — that
        path genuinely can be reached without a session.)

        ``is_user_chat`` is always True here — the call site guards on it —
        so ``evaluate_bypass``'s background_scope branch is unreachable on
        this chain. The parameter stays so that re-opening background turns
        later is a one-line change to the guard, not a plumbing change.

        The except clause is one log line and nothing else. It used to reset the
        nine fields the recorder writes, from a list kept by hand, which was
        already missing ``gate_reason`` on the day it was written and did not
        roll back snapshots at all (leaving orphan rows in
        ``narrative_text_snapshots``). ``record_pool_only`` is now
        all-or-nothing, so there is no list left to drift.
        """
        try:
            await self._retrieval.record_pool_only(
                query_text, user_id, agent_id,
                top_k=top_k,
                anchor_narrative_id=session.current_narrative_id,
                is_user_chat=is_user_chat,
                audit=audit,
                snapshots=snapshots,
            )
        except Exception as e:  # noqa: BLE001 — the observer must not break the observed
            logger.warning(
                f"[narrative.shadow_pool] recording failed (agent={agent_id}): "
                f"{type(e).__name__}: {e} (verdict unaffected; the row keeps "
                f"its pre-slice-0 shape)"
            )

    def _get_continuity_detector(self) -> Optional[ContinuityDetector]:
        """Get the continuity detector (lazy loaded)"""
        if self._continuity_detector is None:
            try:
                self._continuity_detector = ContinuityDetector()
            except Exception as e:
                logger.warning(f"ContinuityDetector initialization failed: {e}")
        return self._continuity_detector
