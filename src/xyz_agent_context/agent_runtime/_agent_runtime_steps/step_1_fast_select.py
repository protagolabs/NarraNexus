"""
@file_name: step_1_fast_select.py
@date: 2026-08-06
@description: Fast-mode replacement for step_1 — BM25 top-1 direct narrative pick.

Fast mode must reach the agent loop without paying step_1's synchronous
LLM round trip (ContinuityDetector + the retrieval LLM tier). This step
keeps the narrative "cheaply present" instead of bypassing it: one BM25
keyword query, top-1, load, done. No LLM ever runs here.

What happens on a miss is the surface's call, carried by
``TurnProfile.narrative_persistence``:

* ``"ephemeral"`` (voice, F28): the turn runs bare — no creation, no
  session writes — so a normal follow-up message continuity-checks
  exactly as if the voice turn never happened.
* ``"durable"`` (persisted chat surfaces): a miss first reuses the
  session's current thread when it was touched within
  ``FAST_REUSE_WINDOW_S`` (small-corpus BM25 is degenerate — the floor
  is a noise filter, not a strength test), and only then falls through
  to a CRUD-only narrative creation. The session continuity anchor
  (last_query / current_narrative_id) is kept consistent on hit, reuse
  and create alike. A fast chat turn must never vanish from history —
  both history endpoints are narrative-scoped, and step_4 persists
  nothing for a bare turn.

What it still MUST do on every hit: ensure the user's ChatModule
instance on the narrative (chat history assembly and turn persistence
both hang off that instance — skipping it would silently break memory).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, AsyncGenerator, Optional

from loguru import logger

from xyz_agent_context.schema import ProgressMessage, ProgressStatus

from .step_1_select_narrative import _ensure_user_chat_instance, _is_user_chat

if TYPE_CHECKING:
    from xyz_agent_context.narrative import NarrativeService, SessionService

    from .context import RunContext


#: How long the session's ``current_narrative_id`` counts as "the live
#: conversation" for sub-floor BM25 misses. Small corpora make BM25
#: degenerate (even a verbatim repeat can score below the floor — the
#: floor is a noise filter, not a strength test, see narrative/config.py),
#: so inside this window a miss reuses the session thread instead of
#: fragmenting the conversation one narrative per turn. The full path's
#: equivalent judgement is continuity's LLM tier; the fast path trades it
#: for this deterministic horizon.
FAST_REUSE_WINDOW_S = 30 * 60


def _is_durable(ctx: "RunContext") -> bool:
    profile = ctx.turn_profile
    return (
        profile is not None
        and getattr(profile, "narrative_persistence", "ephemeral") == "durable"
    )


def _anchor_is_recent(ctx: "RunContext") -> bool:
    """True iff the session points at a thread touched within the reuse
    window. Naive timestamps are assumed UTC (same rule continuity.py
    applies — DB round-trips can strip tzinfo)."""
    from datetime import datetime, timezone

    session = ctx.session
    if session is None or not getattr(session, "current_narrative_id", None):
        return False
    last = getattr(session, "last_query_time", None)
    if last is None:
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    elapsed = (datetime.now(timezone.utc) - last).total_seconds()
    return elapsed <= FAST_REUSE_WINDOW_S


async def _anchor_session(
    ctx: "RunContext",
    session_service: Optional["SessionService"],
    narrative_id: str,
    query: str,
) -> None:
    """Keep the chat continuity anchor consistent for durable fast turns.

    Mirrors the four writes the full select() performs for user-chat
    runs. Without this, the next non-fast turn would judge continuity
    against a half-stale anchor (last_response moved by step_4 while
    current_narrative_id still points at the previous thread).
    """
    if session_service is None or ctx.session is None or not _is_user_chat(ctx):
        return
    from datetime import datetime, timezone

    ctx.session.last_query = query
    ctx.session.current_narrative_id = narrative_id
    ctx.session.query_count += 1
    ctx.session.last_query_time = datetime.now(timezone.utc)
    await session_service.save_session(ctx.session)
    logger.debug(f"[step_1_fast] session anchored to narrative={narrative_id}")


async def step_1_fast_select(
    ctx: "RunContext",
    narrative_service: "NarrativeService",
    session_service: Optional["SessionService"] = None,
) -> AsyncGenerator[ProgressMessage, None]:
    """Pick the background narrative for a fast turn (BM25 top-1, no LLM).

    Fills ``ctx.narrative_list`` and ``ctx.user_chat_instances``. On a
    miss, ephemeral profiles run the turn bare; durable profiles create
    the narrative (CRUD only) so the turn persists.
    """
    yield ProgressMessage(
        step="1",
        title="📚 Narrative Selection (fast)",
        description="BM25 top-1 direct pick...",
        status=ProgressStatus.RUNNING,
        substeps=[],
    )

    if ctx.cancellation:
        ctx.cancellation.raise_if_cancelled()

    # Same retrieval-text preference as the full path: the trigger's clean
    # anchor (sender + body) beats the full execution prompt.
    anchor = (ctx.trigger_extra_data or {}).get("retrieval_anchor")
    query = anchor if anchor and str(anchor).strip() else ctx.input_content

    narrative = await narrative_service.select_fast(ctx.agent_id, ctx.user_id, query)
    retrieval_method = "bm25_fast"

    if narrative is None:
        if _is_durable(ctx) and _is_user_chat(ctx):
            # Sub-floor miss inside a live session: reuse the session
            # thread before creating — small-corpus BM25 misses even
            # verbatim repeats, and one narrative per turn would shatter
            # the conversation (each narrative carries its own ChatModule
            # instance, i.e. its own history).
            if _anchor_is_recent(ctx):
                narrative = await narrative_service.load_narrative_from_db(
                    ctx.session.current_narrative_id
                )
            if narrative is not None:
                retrieval_method = "session_fast"
                logger.info(
                    f"[step_1_fast] miss — reusing session narrative={narrative.id}"
                )
            else:
                narrative = await narrative_service.create_fast(
                    ctx.agent_id, ctx.user_id, query
                )
                retrieval_method = "bm25_fast_created"
                logger.info(f"[step_1_fast] miss — created narrative={narrative.id}")
        else:
            ctx.narrative_list = []
            logger.info("[step_1_fast] no BM25 candidate — running bare")
            yield ProgressMessage(
                step="1",
                title="📚 Narrative Selection (fast)",
                description="No matching narrative — running without background",
                status=ProgressStatus.COMPLETED,
                details={"retrieval_method": "bm25_fast", "hit": False},
            )
            return

    ctx.narrative_list = [narrative]
    ctx.substeps_1.append(
        f"[1.1] ✓ {narrative.narrative_info.name} ({retrieval_method})"
    )

    # History and persistence hang off the user's ChatModule instance in
    # the selected narrative — the fast path must keep that invariant.
    try:
        chat_instance_id = await _ensure_user_chat_instance(
            ctx.agent_id, ctx.user_id, narrative.id
        )
        ctx.user_chat_instances = {narrative.id: chat_instance_id}
    except Exception as e:  # noqa: BLE001 — degraded turn beats a dead turn
        logger.warning(f"[step_1_fast] chat-instance ensure failed: {e}")

    if _is_durable(ctx):
        await _anchor_session(ctx, session_service, narrative.id, query)

    logger.info(f"[step_1_fast] narrative={narrative.id} method={retrieval_method}")
    yield ProgressMessage(
        step="1",
        title="📚 Narrative Selection (fast)",
        description=f"Background: {narrative.narrative_info.name}",
        status=ProgressStatus.COMPLETED,
        details={"retrieval_method": retrieval_method, "hit": True},
    )
