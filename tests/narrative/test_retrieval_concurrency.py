"""
@file_name: test_retrieval_concurrency.py
@date: 2026-08-14
@description: Overlapping two reads must not change which narrative is chosen.

`_retrieve_top_k` used to do three awaits in a row: ensure_defaults,
participant_query, load_pool. The last two are independent and now overlap.

The saving is single-digit milliseconds against a setup phase whose p50 is 8.5
seconds — this is emphatically not a latency fix, and the file says so where it
matters. What it IS is a change to the highest-consequence surface in the
codebase, so the property that matters is that it changed nothing:

* ensure_defaults still runs to completion BEFORE the pool is read. It CREATES
  default narratives when missing; a pool read racing that creation would drop
  them from the BM25 candidate set — a wrong answer, not a slow one.
* both reads still happen, and both results still reach the candidate pool.
* a failure in either is still raised, not swallowed into a half-built pool.
"""
from __future__ import annotations

import asyncio

import pytest

from xyz_agent_context.narrative.models import NarrativeType


class _Recorder:
    """Notes the order in which the three reads start and finish."""

    def __init__(self):
        self.events: list[str] = []

    def mark(self, what: str) -> None:
        self.events.append(what)


def _install(retrieval, rec: _Recorder, *, pool_fails: bool = False,
             participant_fails: bool = False, participant_delay: float = 0.02):
    async def _ensure(agent_id, user_id):
        rec.mark('defaults:start')
        await asyncio.sleep(0.01)
        rec.mark('defaults:end')

    async def _participants(*, user_id, agent_id):
        rec.mark('participants:start')
        if participant_fails:
            raise RuntimeError('participant query exploded')
        await asyncio.sleep(participant_delay)
        rec.mark('participants:end')
        return []

    async def _pool(agent_id, user_id):
        rec.mark('pool:start')
        if pool_fails:
            raise RuntimeError('pool read exploded')
        await asyncio.sleep(0.02)
        rec.mark('pool:end')
        return []

    retrieval._ensure_default_narratives = _ensure  # type: ignore[assignment]
    retrieval._get_participant_narratives = _participants  # type: ignore[assignment]
    retrieval.load_pool = _pool  # type: ignore[assignment]
    retrieval.rank_pool = lambda *a, **k: []  # type: ignore[assignment]


@pytest.fixture
def retrieval(monkeypatch, db_client):
    from xyz_agent_context.narrative._narrative_impl.retrieval import NarrativeRetrieval

    async def _get_db():
        return db_client

    monkeypatch.setattr(
        "xyz_agent_context.utils.db.db_factory.get_db_client", _get_db
    )
    return NarrativeRetrieval(agent_id="a1")


@pytest.mark.asyncio
async def test_defaults_complete_before_the_pool_is_read(retrieval):
    """The one ordering that is a CORRECTNESS constraint, not a preference."""
    rec = _Recorder()
    _install(retrieval, rec)

    await retrieval.retrieve_top_k(
        query="hello", user_id="u1", agent_id="a1", top_k=3,
        narrative_type=NarrativeType.CHAT,
    )

    assert rec.events.index('defaults:end') < rec.events.index('pool:start'), (
        "the pool was read while default narratives were still being created — "
        f"they can be missing from the candidate set: {rec.events}"
    )


@pytest.mark.asyncio
async def test_the_two_independent_reads_actually_overlap(retrieval):
    """Otherwise the change is pure risk with none of its (small) upside."""
    rec = _Recorder()
    _install(retrieval, rec)

    await retrieval.retrieve_top_k(
        query="hello", user_id="u1", agent_id="a1", top_k=3,
        narrative_type=NarrativeType.CHAT,
    )

    # Both start before either finishes.
    first_end = min(rec.events.index('participants:end'), rec.events.index('pool:end'))
    assert rec.events.index('participants:start') < first_end
    assert rec.events.index('pool:start') < first_end


@pytest.mark.asyncio
@pytest.mark.parametrize("which", ["pool", "participants"])
async def test_a_failing_read_is_raised_not_swallowed(retrieval, which):
    """A half-built candidate pool must never be presented as a decision.

    `gather(..., return_exceptions=False)` on purpose: either read failing
    means the candidate set is wrong, and silently routing on the remainder
    would produce a confident answer from incomplete evidence.
    """
    rec = _Recorder()
    _install(
        retrieval, rec,
        pool_fails=(which == "pool"),
        participant_fails=(which == "participants"),
    )

    with pytest.raises(RuntimeError):
        await retrieval.retrieve_top_k(
            query="hello", user_id="u1", agent_id="a1", top_k=3,
            narrative_type=NarrativeType.CHAT,
        )

# --- the columns are only useful if something FILLS them --------------------

@pytest.mark.asyncio
async def test_a_retrieval_fills_the_cost_columns(retrieval):
    """Reviewed gap: the audit tests proved "if the field is set, it reaches the
    table" and nothing proved the field gets set.

    Delete `audit.keyword_ms = ...` and every other test here stays green while
    the column goes permanently NULL — a failure whose only symptom is "that new
    column never has data", noticed by whoever happens to look.
    """
    rec = _Recorder()
    _install(retrieval, rec)

    result = await retrieval.retrieve_top_k(
        query="hello", user_id="u1", agent_id="a1", top_k=3,
        narrative_type=NarrativeType.CHAT,
    )

    # `is not None`, not `> 0`: a stubbed read can finish inside one millisecond
    # and int(...*1000) legitimately yields 0, which in this schema means "ran,
    # was fast" — a different thing from NULL ("did not run").
    assert result.audit is not None
    assert result.audit.keyword_ms is not None, (
        "keyword_ms was never assigned — the column would sit at NULL forever"
    )


@pytest.mark.asyncio
async def test_keyword_ms_excludes_the_participant_read(retrieval):
    """It means "BM25 pool load + rank", and the participant query now runs
    alongside the pool load. Charging a slow participant query to BM25 would
    make this column answer its own question wrongly."""
    rec = _Recorder()
    _install(retrieval, rec, participant_delay=0.25)

    result = await retrieval.retrieve_top_k(
        query="hello", user_id="u1", agent_id="a1", top_k=3,
        narrative_type=NarrativeType.CHAT,
    )

    assert result.audit.keyword_ms < 200, (
        f"keyword_ms={result.audit.keyword_ms}ms absorbed the 250ms participant "
        f"query it merely runs beside"
    )
