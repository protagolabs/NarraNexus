"""
@file_name: test_shadow_pool_record.py
@date: 2026-08-25
@description: A continuity turn must record the BM25 pool it never consulted —
and must decide exactly what it decided before.

WHY (specs/2026-08-25-merged-routing-design.md §2)

The zero-LLM shutter's releasable population is bounded at 6% (lower) to 39%
(upper) of continuity turns — a 3x band that is almost entirely reconstruction
slack, not signal. The band exists for one reason: when continuity says yes,
`NarrativeService.select` returns before the retrieval tier runs, so BM25 never
scores and the audit row carries no pool. Every estimate of "what would the
shutter have said here" is therefore inferred from a pool that was never built.

Slice 0 closes that by running the pool build on continuity turns too, purely
to RECORD it. Nothing about the verdict changes: the turn still lands on
`session.current_narrative_id`, still reports `selection_method="continuous"`.

THE INVARIANT THIS FILE EXISTS FOR

An instrument that changes the thing it measures is worse than no instrument.
`test_the_verdict_is_byte_identical_with_and_without_the_recorder` pins the
decision fields against the recorder being disabled, so a future edit that lets
the shadow pool leak into the outcome turns red here rather than in an arm.

COLUMN SEMANTICS (binding rule #6 — no silent meaning changes)

`gate_short_circuit` means "this turn skipped the judge because the gate said
so". On a shadow row NOTHING was decided by the gate, so it stays NULL exactly
as it is today. The hypothetical verdict goes into `bypass_score_gate` /
`bypass_reason`, which are this batch's own columns and have no legacy readers,
and `pool_is_shadow` marks the row so no aggregate mixes the two populations.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from xyz_agent_context.narrative.models import ConversationSession
from xyz_agent_context.narrative.narrative_service import NarrativeService
from xyz_agent_context.repository.narrative_routing_audit_repository import (
    NarrativeRoutingAuditRepository,
)

pytestmark = pytest.mark.asyncio

AGENT = "agent_shadow"
USER = "user_shadow"


@pytest.fixture
def service(db_client, monkeypatch):
    svc = NarrativeService(agent_id=AGENT, database_client=db_client)
    svc._crud.set_database_client(db_client)
    svc._retrieval.set_database_client(db_client)

    async def _get():
        return db_client

    monkeypatch.setattr("xyz_agent_context.utils.db.db_factory.get_db_client", _get)

    async def _stub_judge(**kw):
        raise AssertionError("the judge must never run on a continuity turn")

    monkeypatch.setattr(svc._retrieval, "_llm_judge_unified", _stub_judge)
    return svc


def _continuous(svc, monkeypatch, verdict: bool = True):
    class _Detector:
        async def detect(self, **kw):
            from xyz_agent_context.narrative.models import ContinuityResult

            return ContinuityResult(
                is_continuous=verdict, confidence=0.93, reason="stub"
            )

    monkeypatch.setattr(svc, "_get_continuity_detector", lambda: _Detector())


def _session(anchor: str):
    now = datetime.now(timezone.utc)
    return ConversationSession(
        session_id="sess_shadow", user_id=USER, agent_id=AGENT,
        created_at=now, last_query_time=now,
        last_query="上一句", last_response="上一条回复",
        current_narrative_id=anchor,
    )


async def _seed(service):
    """An anchor thread plus competition, so the pool is worth recording."""
    anchor = await service.create_narrative(
        agent_id=AGENT, user_id=USER, title="部署脚本报错排查", description="",
    )
    others = [
        await service.create_narrative(
            agent_id=AGENT, user_id=USER, title=f"纽约餐厅推荐 第{i}轮", description="",
        )
        for i in range(3)
    ]
    return anchor, others


async def _row(db_client):
    rows = await NarrativeRoutingAuditRepository(db_client).recent(agent_id=AGENT)
    assert rows, "no audit row was written"
    return rows[0]


# ---------------- the instrument ---------------------------------------------


async def test_a_continuity_turn_now_records_the_pool_it_never_consulted(
    service, db_client, monkeypatch
):
    anchor, others = await _seed(service)
    _continuous(service, monkeypatch)

    await service.select(
        AGENT, USER, "那第二步呢", session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    row = await _row(db_client)
    assert row["selection_method"] == "continuous"
    assert row["candidates"], "the shadow pool was not recorded"
    ids = {c["narrative_id"] for c in row["candidates"]}
    assert anchor.id in ids
    assert {n.id for n in others} <= ids, (
        "a partial pool cannot be replayed — IDF and avgdl are computed over "
        "the whole candidate set"
    )
    assert row["gate_top1_raw"] is not None, "the pool was recorded but not scored"


async def test_the_shadow_row_is_marked_as_such(service, db_client, monkeypatch):
    anchor, _ = await _seed(service)
    _continuous(service, monkeypatch)

    await service.select(
        AGENT, USER, "那第二步呢", session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    row = await _row(db_client)
    assert row["pool_is_shadow"], (
        "an unmarked shadow row makes every gate aggregate mix two populations"
    )
    assert row["bypass_reason"], "the hypothetical shutter verdict was not recorded"


async def test_the_gate_verdict_column_stays_null_on_a_shadow_row(
    service, db_client, monkeypatch
):
    """`gate_short_circuit` means "the GATE skipped the judge" — binding rule #6.

    On a continuity turn the gate decided nothing, so filling that column would
    silently redefine it for every existing reader. The hypothetical verdict
    lives in this batch's own columns instead.
    """
    anchor, _ = await _seed(service)
    _continuous(service, monkeypatch)

    await service.select(
        AGENT, USER, "那第二步呢", session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    row = await _row(db_client)
    assert row["gate_short_circuit"] is None
    assert row["bypass_score_gate"] is not None


# ---------------- the invariant ---------------------------------------------


async def test_the_verdict_is_byte_identical_with_and_without_the_recorder(
    service, db_client, monkeypatch
):
    """The whole point: an instrument that moves the needle is not an instrument."""
    anchor, _ = await _seed(service)
    _continuous(service, monkeypatch)

    decided = ("selection_method", "chosen_narrative_id", "is_new",
               "retrieval_method", "continuity_ran", "continuity_is_continuous")

    result_on = await service.select(
        AGENT, USER, "那第二步呢", session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )
    row_on = await _row(db_client)

    # Same turn with the recorder removed — the branch as it was before slice 0.
    monkeypatch.setattr(
        service._retrieval, "record_pool_only",
        AsyncMock(side_effect=AssertionError("recorder should be off")),
        raising=False,
    )
    monkeypatch.setattr(service, "_record_shadow_pool", AsyncMock(return_value=None))

    result_off = await service.select(
        AGENT, USER, "那第二步呢", session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )
    row_off = await _row(db_client)

    assert result_on.selection_method == result_off.selection_method == "continuous"
    assert [n.id for n in result_on.narratives] == [n.id for n in result_off.narratives]
    for field in decided:
        assert row_on[field] == row_off[field], (
            f"the recorder changed `{field}`: {row_on[field]!r} vs {row_off[field]!r}"
        )
    # ...and only the recorder's own columns differ
    assert row_off["candidates"] == []
    assert row_on["candidates"]


async def test_a_recorder_failure_never_breaks_the_turn(
    service, db_client, monkeypatch
):
    """The observer must not break the observed (audit repo's stated rule).

    Deliberately narrow: the guard wraps ONLY the instrument call. A failure in
    the decision path must still propagate.
    """
    anchor, _ = await _seed(service)
    _continuous(service, monkeypatch)

    async def _boom(*a, **kw):
        raise RuntimeError("pool read exploded")

    monkeypatch.setattr(service._retrieval, "record_pool_only", _boom, raising=False)

    result = await service.select(
        AGENT, USER, "那第二步呢", session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )
    assert result.selection_method == "continuous"
    assert result.narratives and result.narratives[0].id == anchor.id
    row = await _row(db_client)
    assert row["selection_method"] == "continuous"
    assert not row["candidates"], "a failed recorder must not half-fill the pool"


async def test_the_recorder_is_awaited_not_fired_and_forgotten(
    service, db_client, monkeypatch
):
    """Incident lesson #2: a bare `create_task` swallows its own exceptions and
    races the audit write. By the time `select` returns, the pool must be ON
    the audit object — not scheduled to arrive later."""
    anchor, _ = await _seed(service)
    _continuous(service, monkeypatch)

    seen: dict = {}
    original = service._retrieval.record_pool_only

    async def _spy(*a, **kw):
        await original(*a, **kw)
        audit = kw.get("audit") or a[-2]
        seen["candidates_at_return"] = len(audit.candidates)

    monkeypatch.setattr(service._retrieval, "record_pool_only", _spy, raising=False)

    await service.select(
        AGENT, USER, "那第二步呢", session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )
    assert seen.get("candidates_at_return", 0) > 0


async def test_a_non_continuity_turn_is_not_marked_shadow(
    service, db_client, monkeypatch
):
    """The flag must separate the two populations in BOTH directions."""
    anchor, _ = await _seed(service)
    _continuous(service, monkeypatch, verdict=False)
    monkeypatch.setattr(
        service._retrieval, "_llm_judge_unified",
        AsyncMock(return_value={"matched_type": "none", "matched_id": None,
                                "reason": "stub"}),
    )

    await service.select(
        AGENT, USER, "完全不相干的新话题:帮我订机票", session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    row = await _row(db_client)
    assert row["selection_method"] != "continuous"
    assert not row["pool_is_shadow"]
    assert row["candidates"]


async def test_the_shadow_column_is_registered_on_both_dialects() -> None:
    from xyz_agent_context.utils.db.schema_registry import TABLES

    col = {c.name: c for c in TABLES["narrative_routing_audit"].columns}.get(
        "pool_is_shadow"
    )
    assert col is not None, "narrative_routing_audit.pool_is_shadow not registered"
    assert col.sqlite_type and col.mysql_type
    assert col.nullable, (
        "prod rows predate this column; NOT NULL would fail ALTER TABLE on a "
        "live table (binding rule #6)"
    )
