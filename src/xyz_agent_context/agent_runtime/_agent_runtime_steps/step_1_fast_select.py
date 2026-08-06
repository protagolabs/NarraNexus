"""
@file_name: step_1_fast_select.py
@date: 2026-08-06
@description: Fast-mode replacement for step_1 — BM25 top-1 direct narrative pick.

Voice fast mode (F28) must reach the agent loop without paying step_1's
synchronous LLM round trip (ContinuityDetector + the retrieval LLM tier).
This step keeps the narrative "cheaply present" instead of bypassing it:
one BM25 keyword query, top-1, load, done. No LLM, no narrative creation,
and — deliberately — NO session writes: the function does not even take a
session_service, so a normal follow-up message continuity-checks exactly
as if the voice turn never happened.

What it still MUST do: ensure the user's ChatModule instance on the hit
narrative (chat history assembly and turn persistence both hang off that
instance — skipping it would silently break memory for voice turns).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, AsyncGenerator

from loguru import logger

from xyz_agent_context.schema import ProgressMessage, ProgressStatus

from .step_1_select_narrative import _ensure_user_chat_instance

if TYPE_CHECKING:
    from xyz_agent_context.narrative import NarrativeService

    from .context import RunContext


async def step_1_fast_select(
    ctx: "RunContext",
    narrative_service: "NarrativeService",
) -> AsyncGenerator[ProgressMessage, None]:
    """Pick the background narrative for a fast turn (BM25 top-1, no LLM).

    Fills ``ctx.narrative_list`` ([narrative] or []) and
    ``ctx.user_chat_instances`` (only on a hit). A miss runs the turn
    bare — nothing is created.
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

    if narrative is None:
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
    ctx.substeps_1.append(f"[1.1] ✓ {narrative.narrative_info.name} (bm25_fast)")

    # History and persistence hang off the user's ChatModule instance in
    # the selected narrative — the fast path must keep that invariant.
    try:
        chat_instance_id = await _ensure_user_chat_instance(
            ctx.agent_id, ctx.user_id, narrative.id
        )
        ctx.user_chat_instances = {narrative.id: chat_instance_id}
    except Exception as e:  # noqa: BLE001 — degraded turn beats a dead turn
        logger.warning(f"[step_1_fast] chat-instance ensure failed: {e}")

    logger.info(f"[step_1_fast] narrative={narrative.id} method=bm25_fast")
    yield ProgressMessage(
        step="1",
        title="📚 Narrative Selection (fast)",
        description=f"Background: {narrative.narrative_info.name}",
        status=ProgressStatus.COMPLETED,
        details={"retrieval_method": "bm25_fast", "hit": True},
    )
