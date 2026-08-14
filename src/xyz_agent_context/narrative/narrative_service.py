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

from typing import List, Optional, Tuple, TYPE_CHECKING

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
        self, agent_id: str, user_id: str, query: str
    ) -> Optional[Narrative]:
        """BM25 top-1 direct pick — the fast-mode (F28) narrative path.

        Zero LLM, zero creation, zero session writes: one keyword search
        (top_k=1) plus a CRUD load. None when nothing scores or the row
        vanished between search and load. What the caller does on a miss
        is the surface's call: voice runs the turn bare, durable chat
        surfaces fall through to ``create_fast`` below. The continuity /
        LLM tiers remain exclusive to the full select().
        """
        from .config import config

        results = await self._retrieval.keyword_search(
            query=query, user_id=user_id, agent_id=agent_id, top_k=1
        )
        # Same raw-score floor the full path uses before direct-return: the
        # fast path has no LLM arbitration tier, so a sub-floor top-1 (a
        # one-word accidental overlap) is a miss, not a background pick.
        if not results or results[0].raw_score < config.NARRATIVE_MATCH_RAW_FLOOR:
            return None
        return await self._crud.load_by_id(results[0].narrative_id)

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

        # Continuity detection — wrapped in timed() so its LLM call is visible
        # as a discrete slice of step.1 instead of getting lumped into
        # the "everything else" bucket.
        is_continuous = False
        continuity_reason = ""
        continuity_ran = False
        continuity_confidence: Optional[float] = None
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

        if not narratives:
            # Not continuous or continuity detection failed: retrieve Top-K
            with timed("narrative.retrieve_top_k"):
                retrieval_result = await self._retrieval.retrieve_top_k(
                    query=query_text,
                    user_id=user_id,
                    agent_id=agent_id,
                    top_k=max_narratives
                )
            narratives = retrieval_result.narratives
            selection_reason = retrieval_result.selection_reason
            selection_method = retrieval_result.selection_method
            retrieval_method = retrieval_result.retrieval_method
            audit = retrieval_result.audit
            audit_snapshots = retrieval_result.audit_snapshots
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

        # Update Session (using main Narrative).
        # Only user-initiated runs (chat) write to last_query / last_response /
        # current_narrative_id — background trigger runs (job / message_bus /
        # lark / callback) must leave these fields untouched so the *next*
        # user message gets its continuity judged against the previous user
        # exchange, not against whatever cron job or bus ping ran in between.
        if session and narratives and is_user_chat:
            from datetime import datetime, timezone
            session.last_query = query_text
            session.current_narrative_id = narratives[0].id
            session.query_count += 1
            session.last_query_time = datetime.now(timezone.utc)

        logger.info(f"[NarrativeSelect] completed: {len(narratives)} Narratives, method={selection_method}")

        if audit is not None:
            audit.trigger = trigger
            audit.is_user_chat = is_user_chat
            audit.continuity_ran = continuity_ran
            audit.continuity_is_continuous = is_continuous if continuity_ran else None
            audit.continuity_confidence = continuity_confidence
            audit.continuity_reason = continuity_reason
            await self._write_audit(audit, audit_snapshots)

        return NarrativeSelectionResult(
            narratives=narratives,
            selection_reason=selection_reason,
            selection_method=selection_method,
            is_new=(selection_method == "new_created"),
            best_score=None,
            retrieval_method=retrieval_method,
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

    def _get_continuity_detector(self) -> Optional[ContinuityDetector]:
        """Get the continuity detector (lazy loaded)"""
        if self._continuity_detector is None:
            try:
                self._continuity_detector = ContinuityDetector()
            except Exception as e:
                logger.warning(f"ContinuityDetector initialization failed: {e}")
        return self._continuity_detector
