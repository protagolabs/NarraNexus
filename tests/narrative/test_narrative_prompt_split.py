"""
@file_name: test_narrative_prompt_split.py
@author: NarraNexus
@date: 2026-07-25
@description: R4a/R4c narrative template split — the main narrative prompt is
split into a byte-stable half (system prompt) and a per-turn volatile half
(turn context: name + updated_at + current_summary). The split must move
content, never drop it (铁律 #16), and the stable half must be byte-identical
across turns whose only difference is the volatile fields.

R4c additions (experiment E2, 2026-07-25):
- Name is volatile (the updater rewrites it every LLM update: draft
  truncated name -> finalized name) and lives in the TURN half.
- Timestamps render through ONE canonical formatter, so an in-memory
  narrative (tz-aware, microseconds) and its DB round-trip twin (second
  precision, naive or tz-aware) produce byte-identical stable blocks.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from xyz_agent_context.narrative.models import (
    Narrative,
    NarrativeActor,
    NarrativeActorType,
    NarrativeInfo,
    NarrativeType,
)
from xyz_agent_context.narrative._narrative_impl.prompt_builder import (
    PromptBuilder,
    _canonical_timestamp,
)


def _narrative(
    summary: str,
    updated: datetime,
    name: str = "Split test",
    created: datetime | None = None,
) -> Narrative:
    return Narrative(
        id="narr_split",
        type=NarrativeType.CHAT,
        agent_id="agent_split",
        narrative_info=NarrativeInfo(
            name=name,
            description="template split",
            current_summary=summary,
            actors=[NarrativeActor(id="agent_split", type=NarrativeActorType.AGENT)],
        ),
        event_ids=[],
        created_at=created or datetime(2026, 7, 20, 8, 0, 0),
        updated_at=updated,
    )


@pytest.mark.asyncio
async def test_stable_render_is_full_render_minus_exactly_the_volatile_lines():
    """Split correctness hard standard: the stable half differs from the
    legacy full render by EXACTLY the three volatile lines (Name /
    Updated At / Current Summary) — nothing else was reworded, reordered,
    or dropped."""
    nar = _narrative("Topic: split correctness", datetime(2026, 7, 25, 10, 0, 0))

    full = await PromptBuilder.build_main_prompt(nar)
    stable = await PromptBuilder.build_main_prompt(nar, include_volatile=False)

    expected_stable = full.replace(
        f"- Updated At: {_canonical_timestamp(nar.updated_at)}\n", ""
    ).replace(
        f"- Name: {nar.narrative_info.name}\n", ""
    ).replace(
        f"- Current Summary: {nar.narrative_info.current_summary}\n", ""
    )
    assert stable == expected_stable


@pytest.mark.asyncio
async def test_turn_render_carries_all_volatile_fields():
    nar = _narrative("Topic: volatile payload", datetime(2026, 7, 25, 10, 0, 0))

    turn = await PromptBuilder.build_turn_prompt(nar)

    assert turn.startswith("## Current narrative state")
    assert f"- Name: {nar.narrative_info.name}" in turn
    assert _canonical_timestamp(nar.updated_at) in turn
    assert nar.narrative_info.current_summary in turn


@pytest.mark.asyncio
async def test_stable_render_byte_identical_when_only_volatile_fields_change():
    """Two consecutive turns (summary regenerated, updated_at bumped, name
    finalized from its draft) must produce a byte-identical stable half —
    this is the cacheable prefix."""
    t1 = _narrative(
        "Topic: first turn",
        datetime(2026, 7, 25, 10, 0, 0),
        name="Remember the word: quince-4...",  # draft (query-truncated)
    )
    t2 = _narrative(
        "Topic: second turn, regenerated",
        datetime(2026, 7, 25, 10, 5, 0),
        name="Remember the word: quince-4",  # finalized by the updater
    )

    stable_1 = await PromptBuilder.build_main_prompt(t1, include_volatile=False)
    stable_2 = await PromptBuilder.build_main_prompt(t2, include_volatile=False)
    assert stable_1 == stable_2

    turn_1 = await PromptBuilder.build_turn_prompt(t1)
    turn_2 = await PromptBuilder.build_turn_prompt(t2)
    assert turn_1 != turn_2  # the volatile half is where the difference lives


# =========================================================================
# R4c: canonical timestamp — in-memory vs DB round-trip byte equivalence
# =========================================================================

# The same instant, as each serialization path materializes it (E2 §3):
_IN_MEMORY = datetime(2026, 7, 25, 20, 8, 39, 367468, tzinfo=timezone.utc)  # fresh creation
_DB_AWARE = datetime(2026, 7, 25, 20, 8, 39, tzinfo=timezone.utc)  # driver returns tz-aware
_DB_NAIVE = datetime(2026, 7, 25, 20, 8, 39)  # driver returns naive (stored UTC)


def test_canonical_timestamp_collapses_all_round_trip_variants():
    rendered = {
        _canonical_timestamp(_IN_MEMORY),
        _canonical_timestamp(_DB_AWARE),
        _canonical_timestamp(_DB_NAIVE),
    }
    assert rendered == {"2026-07-25 20:08:39 UTC"}


def test_canonical_timestamp_converts_non_utc_zones_to_utc():
    from datetime import timedelta

    cst = timezone(timedelta(hours=8))
    assert (
        _canonical_timestamp(datetime(2026, 7, 26, 4, 8, 39, 500, tzinfo=cst))
        == "2026-07-25 20:08:39 UTC"
    )


@pytest.mark.asyncio
async def test_stable_block_byte_identical_in_memory_vs_db_round_trip():
    """The E2 first-divergence fix: Round A (in-memory narrative, micro-
    second created_at) and Round B (DB re-read, second precision) must
    render byte-identical stable blocks."""
    updated = datetime(2026, 7, 25, 10, 0, 0)
    in_memory = _narrative("Topic: rt", updated, created=_IN_MEMORY)
    db_aware = _narrative("Topic: rt", updated, created=_DB_AWARE)
    db_naive = _narrative("Topic: rt", updated, created=_DB_NAIVE)

    renders = {
        await PromptBuilder.build_main_prompt(n, include_volatile=False)
        for n in (in_memory, db_aware, db_naive)
    }
    assert len(renders) == 1
