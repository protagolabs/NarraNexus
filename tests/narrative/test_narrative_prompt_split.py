"""
@file_name: test_narrative_prompt_split.py
@author: NarraNexus
@date: 2026-07-25
@description: R4a narrative template split — the main narrative prompt is
split into a byte-stable half (system prompt) and a per-turn volatile half
(turn context: updated_at + current_summary). The split must move content,
never drop it (铁律 #16), and the stable half must be byte-identical across
turns whose only difference is the volatile fields.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from xyz_agent_context.narrative.models import (
    Narrative,
    NarrativeActor,
    NarrativeActorType,
    NarrativeInfo,
    NarrativeType,
)
from xyz_agent_context.narrative._narrative_impl.prompt_builder import PromptBuilder


def _narrative(summary: str, updated: datetime) -> Narrative:
    return Narrative(
        id="narr_split",
        type=NarrativeType.CHAT,
        agent_id="agent_split",
        narrative_info=NarrativeInfo(
            name="Split test",
            description="template split",
            current_summary=summary,
            actors=[NarrativeActor(id="agent_split", type=NarrativeActorType.AGENT)],
        ),
        event_ids=[],
        created_at=datetime(2026, 7, 20, 8, 0, 0),
        updated_at=updated,
    )


@pytest.mark.asyncio
async def test_stable_render_is_full_render_minus_exactly_the_volatile_lines():
    """Split correctness hard standard: the stable half differs from the
    legacy full render by EXACTLY the two volatile lines — nothing else
    was reworded, reordered, or dropped."""
    nar = _narrative("Topic: split correctness", datetime(2026, 7, 25, 10, 0, 0))

    full = await PromptBuilder.build_main_prompt(nar)
    stable = await PromptBuilder.build_main_prompt(nar, include_volatile=False)

    expected_stable = full.replace(
        f"- Updated At: {nar.updated_at}\n", ""
    ).replace(
        f"- Current Summary: {nar.narrative_info.current_summary}\n", ""
    )
    assert stable == expected_stable


@pytest.mark.asyncio
async def test_turn_render_carries_both_volatile_fields():
    nar = _narrative("Topic: volatile payload", datetime(2026, 7, 25, 10, 0, 0))

    turn = await PromptBuilder.build_turn_prompt(nar)

    assert turn.startswith("## Current narrative state")
    assert str(nar.updated_at) in turn
    assert nar.narrative_info.current_summary in turn


@pytest.mark.asyncio
async def test_stable_render_byte_identical_when_only_volatile_fields_change():
    """Two consecutive turns (summary regenerated, updated_at bumped) must
    produce a byte-identical stable half — this is the cacheable prefix."""
    t1 = _narrative("Topic: first turn", datetime(2026, 7, 25, 10, 0, 0))
    t2 = _narrative("Topic: second turn, regenerated", datetime(2026, 7, 25, 10, 5, 0))

    stable_1 = await PromptBuilder.build_main_prompt(t1, include_volatile=False)
    stable_2 = await PromptBuilder.build_main_prompt(t2, include_volatile=False)
    assert stable_1 == stable_2

    turn_1 = await PromptBuilder.build_turn_prompt(t1)
    turn_2 = await PromptBuilder.build_turn_prompt(t2)
    assert turn_1 != turn_2  # the volatile half is where the difference lives
