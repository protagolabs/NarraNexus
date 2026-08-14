"""
@file_name: test_fast_path_service.py
@date: 2026-08-14
@description: Real-service locks for the fast-path narrative primitives.

The step-level tests all inject AsyncMocks, which is how the small-corpus
fragmentation bug slipped through review. These run the REAL
NarrativeService against the isolated test DB and lock:

* create_fast persists a loadable narrative carrying the BM25 routing
  surface (title from the query, keywords, topic hint).
* The small-corpus regime is real: right after creation, the verbatim
  same query can still miss select_fast (BM25 IDF is degenerate on a
  tiny corpus) — which is exactly why step_1_fast_select must reuse the
  session thread instead of creating again (locked at the step level in
  test_step_1_fast_select.py).
"""
from __future__ import annotations

import pytest

from xyz_agent_context.narrative import NarrativeService

AGENT = "agent_fastsvc"
USER = "user_fastsvc"


def _const(v):
    async def _f(*a, **k):
        return v

    return _f


@pytest.fixture
def service(db_client, monkeypatch):
    svc = NarrativeService(agent_id=AGENT, database_client=db_client)
    svc._crud.set_database_client(db_client)
    svc._retrieval.set_database_client(db_client)
    monkeypatch.setattr(
        "xyz_agent_context.utils.db.db_factory.get_db_client",
        _const(db_client),
    )
    return svc


@pytest.mark.asyncio
async def test_create_fast_persists_with_routing_surface(service):
    query = "please help me renew my passport before the trip"
    narrative = await service.create_fast(AGENT, USER, query)

    loaded = await service.load_narrative_from_db(narrative.id)
    assert loaded is not None
    assert loaded.id == narrative.id
    assert query.startswith(loaded.narrative_info.name[:10])
    assert narrative.topic_keywords


@pytest.mark.asyncio
async def test_select_fast_empty_corpus_short_query(service):
    # Empty corpus + short query: no pick, no relation, and the silence
    # is NOT trusted as a new topic (elliptical follow-ups score zero).
    res = await service.select_fast(AGENT, USER, "ok great")
    assert res.narrative is None
    assert res.related is False
    assert res.suggests_new_thread is False
    assert res.top1_raw is None


@pytest.mark.asyncio
async def test_select_fast_trusted_silence_on_long_query(service):
    # A query past FAST_NEW_THREAD_MIN_QUERY_CHARS with zero overlap is
    # trusted as a genuinely new topic.
    long_query = "please compare the health insurance options for the new hires"
    from xyz_agent_context.narrative.config import config

    assert len(long_query) >= config.FAST_NEW_THREAD_MIN_QUERY_CHARS
    res = await service.select_fast(AGENT, USER, long_query)
    assert res.narrative is None
    assert res.suggests_new_thread is True


@pytest.mark.asyncio
async def test_select_fast_hit_is_the_created_narrative(service):
    created = await service.create_fast(
        AGENT, USER, "renew my passport before the trip"
    )
    res = await service.select_fast(
        AGENT, USER, "renew my passport before the trip"
    )
    # Small-corpus BM25 may legitimately miss even the verbatim query
    # (IDF degeneracy) — that reality is exactly why the step layer's
    # anchor-reuse branch exists. But when it DOES pick, it must be the
    # right row.
    if res.narrative is not None:
        assert res.narrative.id == created.id


@pytest.mark.asyncio
async def test_select_fast_anchor_override_uses_strong_floor(service, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    # A mid-strength score (above the noise floor, below the override
    # floor) picks normally but must NOT be allowed to steal a live
    # anchor — yet it still reports `related`, so the step layer reuses
    # the anchor instead of creating. Thresholds live in config.py.
    from xyz_agent_context.narrative.config import config

    score = (config.NARRATIVE_MATCH_RAW_FLOOR + config.FAST_ANCHOR_OVERRIDE_FLOOR) / 2

    async def _fake_search(**_kw):
        return [SimpleNamespace(narrative_id="n1", raw_score=score)]

    monkeypatch.setattr(service._retrieval, "keyword_search", _fake_search)
    loaded = SimpleNamespace(id="n1")
    monkeypatch.setattr(service._crud, "load_by_id", AsyncMock(return_value=loaded))

    plain = await service.select_fast(AGENT, USER, "q")
    assert plain.narrative is loaded
    assert plain.top1_raw == score

    guarded = await service.select_fast(AGENT, USER, "q", against_live_anchor=True)
    assert guarded.narrative is None
    assert guarded.related is True
    assert guarded.suggests_new_thread is False
    assert guarded.top1_raw == score
